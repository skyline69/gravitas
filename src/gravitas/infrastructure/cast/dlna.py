"""CastTarget and CastDiscovery for DLNA / UPnP AV media renderers.

Nearly every smart TV of the last fifteen years is one (Panasonic VIERA, LG,
Samsung, Sony), and so are many speakers and set-top boxes. The protocol is
unauthenticated SOAP over HTTP on the LAN:

* **Discovery** is SSDP: an M-SEARCH multicast to 239.255.255.250:1900 for
  `MediaRenderer:1`, answered by each renderer with the URL of its device
  description. The description names it (`friendlyName`) and says where its
  services' control URLs are -- they differ per vendor (a Panasonic serves
  AVTransport at /dmr/control_2), so nothing here assumes a path.
* **Playback** is AVTransport: SetAVTransportURI with a DIDL-Lite item naming
  the URL and its MIME type, then Play. The renderer fetches the URL itself.
  GetTransportInfo / GetPositionInfo report what it is doing, Seek takes a
  REL_TIME, Pause / Stop do what they say.
* **Volume** is RenderingControl.

A renderer plays what it is told only if it can decode it: there is no
transcoding here, and a 1080p TV handed a 4K HEVC file reports an error or
plays nothing. That surfaces as a failed load (see CastSession).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import socket
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

import httpx

from gravitas.domain.errors import CastFailed
from gravitas.domain.models import CastState, CastStatus
from gravitas.domain.ports import CastTarget

_log = logging.getLogger(__name__)

SSDP_ADDRESS = ("239.255.255.250", 1900)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
# How long a single SOAP call may take. A TV on the LAN answers in tens of
# milliseconds; one that takes longer is off, or asleep with its network up.
SOAP_TIMEOUT_S = 5.0
DESCRIPTION_TIMEOUT_S = 3.0
# DLNA flags for the item: byte-range seekable, streaming transfer. Mirrors
# what the relay answers with (segmented_proxy.DLNA_CONTENT_FEATURES).
_PROTOCOL_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
_DEVICE_NS = "{urn:schemas-upnp-org:device-1-0}"
_STATES: dict[str, CastState] = {
    "PLAYING": "playing",
    "PAUSED_PLAYBACK": "paused",
    "PAUSED_RECORDING": "paused",
    "TRANSITIONING": "loading",
    "STOPPED": "stopped",
    "NO_MEDIA_PRESENT": "stopped",
}


def _search_message(mx: int) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDRESS[0]}:{SSDP_ADDRESS[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {RENDERER}\r\n"
        "\r\n"
    ).encode("ascii")


def parse_location(datagram: bytes) -> str | None:
    """The LOCATION header of an SSDP answer, or None for anything else."""
    text = datagram.decode("latin-1", errors="replace")
    if not text.startswith("HTTP/1.1 200"):
        return None
    for line in text.split("\r\n")[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "location" and value.strip():
            return value.strip()
    return None


def hms(seconds: float) -> str:
    """REL_TIME as AVTransport wants it: H:MM:SS."""
    total = max(0, int(seconds))
    return f"{total // 3600}:{total // 60 % 60:02d}:{total % 60:02d}"


def parse_hms(text: str | None) -> float:
    """REL_TIME / TrackDuration back to seconds. Renderers answer
    "NOT_IMPLEMENTED", an empty string, or a fraction after the seconds
    ("0:01:02.500"); all but the last are 0."""
    match = re.fullmatch(r"\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?\s*", text or "")
    if match is None:
        return 0.0
    hours, minutes, secs, fraction = match.groups()
    value = float(int(hours) * 3600 + int(minutes) * 60 + int(secs))
    if fraction:
        value += float(f"0.{fraction}")
    return value


def _field(xml: str, name: str) -> str | None:
    match = re.search(rf"<(?:\w+:)?{name}>(.*?)</(?:\w+:)?{name}>", xml, re.S)
    return match.group(1) if match else None


def _route_address(host: str) -> str:
    """This machine's address on the route to `host`. A UDP connect sends
    nothing; it only asks the kernel which interface it would use."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((host, SSDP_ADDRESS[1]))
        return str(probe.getsockname()[0])


def didl_item(url: str, *, mime: str, title: str) -> str:
    kind = "audioItem" if mime.startswith("audio/") else "videoItem"
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<upnp:class>object.item.{kind}</upnp:class>"
        f'<res protocolInfo="http-get:*:{escape(mime)}:{_PROTOCOL_FEATURES}">{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


class DlnaRenderer:
    """One renderer, by the control URLs its description named."""

    def __init__(
        self,
        *,
        name: str,
        udn: str,
        av_transport_url: str,
        rendering_control_url: str | None,
        local_address: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._name = name
        self.udn = udn
        self._av_transport_url = av_transport_url
        self._rendering_control_url = rendering_control_url
        self._local_address = local_address
        # A test's mock; production uses httpx's own.
        self._transport = transport

    @property
    def name(self) -> str:
        return self._name

    @property
    def local_address(self) -> str:
        return self._local_address

    async def _call(self, url: str, service: str, action: str, args: str = "") -> str:
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}">{args}</u:{action}></s:Body></s:Envelope>'
        )
        try:
            async with httpx.AsyncClient(
                timeout=SOAP_TIMEOUT_S, transport=self._transport
            ) as client:
                response = await client.post(
                    url,
                    content=body.encode("utf-8"),
                    headers={
                        "Content-Type": 'text/xml; charset="utf-8"',
                        "SOAPACTION": f'"{service}#{action}"',
                    },
                )
        except httpx.HTTPError as exc:
            raise CastFailed(f"{self._name} did not answer.") from exc
        if response.status_code != 200:
            code = _field(response.text, "errorCode")
            detail = f"UPnP error {code}" if code else f"HTTP {response.status_code}"
            raise CastFailed(f"{self._name} refused {action} ({detail}).")
        return response.text

    async def _transport_call(self, action: str, args: str = "") -> str:
        return await self._call(
            self._av_transport_url, AV_TRANSPORT, action, f"<InstanceID>0</InstanceID>{args}"
        )

    async def _rendering_call(self, action: str, args: str = "") -> str:
        if self._rendering_control_url is None:
            raise CastFailed(f"{self._name} has no volume control.")
        return await self._call(
            self._rendering_control_url,
            RENDERING_CONTROL,
            action,
            f"<InstanceID>0</InstanceID><Channel>Master</Channel>{args}",
        )

    async def play(self, url: str, *, mime: str, title: str) -> None:
        metadata = didl_item(url, mime=mime, title=title)
        await self._transport_call(
            "SetAVTransportURI",
            f"<CurrentURI>{escape(url)}</CurrentURI>"
            f"<CurrentURIMetaData>{escape(metadata)}</CurrentURIMetaData>",
        )
        await self.resume()

    async def pause(self) -> None:
        await self._transport_call("Pause")

    async def resume(self) -> None:
        await self._transport_call("Play", "<Speed>1</Speed>")

    async def seek(self, seconds: float) -> None:
        await self._transport_call("Seek", f"<Unit>REL_TIME</Unit><Target>{hms(seconds)}</Target>")

    async def stop(self) -> None:
        await self._transport_call("Stop")

    async def status(self) -> CastStatus:
        # One after the other, not gathered: several renderers serve one SOAP
        # request at a time and answer a second with a 500.
        transport = await self._transport_call("GetTransportInfo")
        position = await self._transport_call("GetPositionInfo")
        state = _STATES.get((_field(transport, "CurrentTransportState") or "").strip(), "loading")
        return CastStatus(
            state=state,
            position=parse_hms(_field(position, "RelTime")),
            duration=parse_hms(_field(position, "TrackDuration")),
        )

    async def volume(self) -> int:
        answer = await self._rendering_call("GetVolume")
        try:
            return int((_field(answer, "CurrentVolume") or "").strip())
        except ValueError as exc:
            raise CastFailed(f"{self._name} sent an unreadable volume.") from exc

    async def set_volume(self, volume: int) -> None:
        level = max(0, min(100, int(volume)))
        await self._rendering_call("SetVolume", f"<DesiredVolume>{level}</DesiredVolume>")

    async def set_muted(self, muted: bool) -> None:
        await self._rendering_call("SetMute", f"<DesiredMute>{int(muted)}</DesiredMute>")


def parse_description(xml: str, location: str) -> tuple[str, str, str, str | None] | None:
    """(friendlyName, UDN, AVTransport control URL, RenderingControl control
    URL) of the first media renderer in a device description, or None. The
    renderer may be an embedded device (a TV's description lists a server and
    a renderer side by side), so every device is looked at."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    base = (root.findtext(f"{_DEVICE_NS}URLBase") or "").strip() or location
    for device in root.iter(f"{_DEVICE_NS}device"):
        if "MediaRenderer" not in (device.findtext(f"{_DEVICE_NS}deviceType") or ""):
            continue
        controls: dict[str, str] = {}
        for service in device.iter(f"{_DEVICE_NS}service"):
            kind = service.findtext(f"{_DEVICE_NS}serviceType") or ""
            control = (service.findtext(f"{_DEVICE_NS}controlURL") or "").strip()
            if control:
                controls[kind.rsplit(":", 2)[-2] if kind.count(":") >= 2 else kind] = urljoin(
                    base, control
                )
        if "AVTransport" not in controls:
            continue
        name = (device.findtext(f"{_DEVICE_NS}friendlyName") or "").strip() or (
            urlsplit(location).hostname or "TV"
        )
        udn = (device.findtext(f"{_DEVICE_NS}UDN") or "").strip() or location
        return name, udn, controls["AVTransport"], controls.get("RenderingControl")
    return None


class _SsdpListener(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.locations: list[str] = []

    def datagram_received(self, data: bytes, addr: tuple[str | object, int]) -> None:
        location = parse_location(data)
        if location is not None and location not in self.locations:
            self.locations.append(location)


class SsdpDiscovery:
    """Finds DLNA media renderers on the LAN with an SSDP search."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def _search(self, listen_s: float) -> list[str]:
        loop = asyncio.get_running_loop()
        try:
            datagrams, listener = await loop.create_datagram_endpoint(
                _SsdpListener, local_addr=("0.0.0.0", 0), family=socket.AF_INET
            )
        except OSError as exc:
            _log.warning("cannot search for TVs: %s", exc)
            return []
        try:
            mx = max(1, int(listen_s))
            # Twice: SSDP is UDP, and one lost datagram would hide a TV.
            for _ in range(2):
                with contextlib.suppress(OSError):
                    datagrams.sendto(_search_message(mx), SSDP_ADDRESS)
                await asyncio.sleep(listen_s / 2)
        finally:
            datagrams.close()
        return listener.locations

    async def _describe(self, client: httpx.AsyncClient, location: str) -> DlnaRenderer | None:
        try:
            response = await client.get(location)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _log.debug("renderer at %s did not describe itself: %s", location, exc)
            return None
        described = parse_description(response.text, location)
        if described is None:
            return None
        name, udn, av_transport, rendering = described
        host = urlsplit(av_transport).hostname or ""
        try:
            local = _route_address(host)
        except OSError as exc:
            _log.debug("no route to renderer %s at %s: %s", name, host, exc)
            return None
        return DlnaRenderer(
            name=name,
            udn=udn,
            av_transport_url=av_transport,
            rendering_control_url=rendering,
            local_address=local,
            transport=self._transport,
        )

    async def discover(self, listen_s: float) -> list[CastTarget]:
        locations = await self._search(listen_s)
        async with httpx.AsyncClient(
            timeout=DESCRIPTION_TIMEOUT_S, transport=self._transport
        ) as client:
            found = await asyncio.gather(*(self._describe(client, loc) for loc in locations))
        # A renderer answers once per interface it listens on, and some send
        # one answer per service: one entry per device.
        unique: dict[str, DlnaRenderer] = {}
        for renderer in found:
            if renderer is not None and renderer.udn not in unique:
                unique[renderer.udn] = renderer
        _log.info("found %d TV(s): %s", len(unique), ", ".join(r.name for r in unique.values()))
        return sorted(unique.values(), key=lambda r: r.name.lower())
