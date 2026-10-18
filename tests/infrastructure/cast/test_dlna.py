from html import unescape

import httpx
import pytest
import respx

from gravitas.domain.errors import CastFailed
from gravitas.infrastructure.cast.dlna import (
    DlnaRenderer,
    SsdpDiscovery,
    didl_item,
    hms,
    parse_description,
    parse_hms,
    parse_location,
)

# A Panasonic TX-40CS630's own description, trimmed: a renderer whose
# AVTransport lives at /dmr/control_2, which is why nothing assumes a path.
PANASONIC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>40CS630_Series</friendlyName>
    <UDN>uuid:4D454930-0100-1000-8001-20C6EBBB7E13</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <controlURL>/dmr/control_0</controlURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <controlURL>/dmr/control_1</controlURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>/dmr/control_2</controlURL>
      </service>
    </serviceList>
  </device>
</root>"""

LOCATION = "http://192.168.2.240:55000/dmr/ddd.xml"
AVT = "http://192.168.2.240:55000/dmr/control_2"
RC = "http://192.168.2.240:55000/dmr/control_0"


def renderer() -> DlnaRenderer:
    return DlnaRenderer(
        name="40CS630_Series",
        udn="uuid:tv",
        av_transport_url=AVT,
        rendering_control_url=RC,
        local_address="192.168.2.10",
    )


def soap_answer(body: str) -> httpx.Response:
    return httpx.Response(
        200,
        text=(
            '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<s:Body>{body}</s:Body></s:Envelope>"
        ),
    )


def test_an_ssdp_answer_gives_its_location() -> None:
    answer = (
        b"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age=1800\r\n"
        b"Location: http://192.168.2.240:55000/dmr/ddd.xml\r\n"
        b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n"
    )
    assert parse_location(answer) == LOCATION
    # Another machine's search, seen on the multicast group, is not an answer.
    assert parse_location(b"M-SEARCH * HTTP/1.1\r\nLOCATION: http://x/\r\n\r\n") is None


def test_times_go_both_ways() -> None:
    assert hms(3725.9) == "1:02:05"
    assert hms(-4) == "0:00:00"
    assert parse_hms("1:02:05") == 3725.0
    assert parse_hms("0:00:01.500") == 1.5
    assert parse_hms("NOT_IMPLEMENTED") == 0.0
    assert parse_hms(None) == 0.0


def test_a_description_names_the_renderer_and_its_control_urls() -> None:
    assert parse_description(PANASONIC, LOCATION) == (
        "40CS630_Series",
        "uuid:4D454930-0100-1000-8001-20C6EBBB7E13",
        AVT,
        RC,
    )


def test_a_device_that_only_serves_media_is_not_a_renderer() -> None:
    server = PANASONIC.replace("MediaRenderer", "MediaServer")
    assert parse_description(server, LOCATION) is None
    assert parse_description("<not xml", LOCATION) is None


def test_the_item_escapes_what_it_names() -> None:
    item = didl_item("http://h/a?x=1&y=2", mime="video/x-matroska", title="Tom & Jerry")
    assert "Tom &amp; Jerry" in item
    assert "http://h/a?x=1&amp;y=2" in item
    assert "http-get:*:video/x-matroska:DLNA.ORG_OP=01" in item
    assert "object.item.videoItem" in item


@respx.mock
async def test_play_hands_the_tv_the_url_then_starts_it() -> None:
    route = respx.post(AVT).mock(return_value=soap_answer(""))
    await renderer().play(
        "http://192.168.2.10:5000/tok/video.mkv", mime="video/x-matroska", title="Ted"
    )
    first, second = (call.request for call in route.calls)
    assert "#SetAVTransportURI" in first.headers["soapaction"]
    sent = first.content.decode()
    assert "<CurrentURI>http://192.168.2.10:5000/tok/video.mkv</CurrentURI>" in sent
    # The DIDL travels escaped inside the SOAP body.
    assert "<dc:title>Ted</dc:title>" in unescape(sent)
    assert "#Play" in second.headers["soapaction"]


@respx.mock
async def test_status_reads_the_transport_and_the_position() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if "GetTransportInfo" in request.headers["soapaction"]:
            return soap_answer(
                "<u:GetTransportInfoResponse><CurrentTransportState>PAUSED_PLAYBACK"
                "</CurrentTransportState></u:GetTransportInfoResponse>"
            )
        return soap_answer(
            "<u:GetPositionInfoResponse><TrackDuration>0:42:10</TrackDuration>"
            "<RelTime>0:03:51</RelTime></u:GetPositionInfoResponse>"
        )

    respx.post(AVT).mock(side_effect=answer)
    status = await renderer().status()
    assert (status.state, status.position, status.duration) == ("paused", 231.0, 2530.0)


@respx.mock
async def test_seek_and_volume_say_what_avtransport_expects() -> None:
    avt = respx.post(AVT).mock(return_value=soap_answer(""))
    rc = respx.post(RC).mock(return_value=soap_answer(""))
    tv = renderer()
    await tv.seek(3725)
    await tv.set_volume(140)
    assert "<Unit>REL_TIME</Unit><Target>1:02:05</Target>" in avt.calls[0].request.content.decode()
    # A TV's volume is 0-100; the player's slider could ask for more.
    assert "<DesiredVolume>100</DesiredVolume>" in rc.calls[0].request.content.decode()


@respx.mock
async def test_a_refusal_carries_the_upnp_error() -> None:
    respx.post(AVT).mock(
        return_value=httpx.Response(
            500, text="<s:Fault><detail><errorCode>701</errorCode></detail></s:Fault>"
        )
    )
    with pytest.raises(CastFailed, match="UPnP error 701"):
        await renderer().pause()


@respx.mock
async def test_a_tv_that_does_not_answer_is_a_cast_failure() -> None:
    respx.post(AVT).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(CastFailed, match="did not answer"):
        await renderer().stop()


@respx.mock
async def test_discovery_lists_each_renderer_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # One TV answering on two interfaces, and a media server that is not one.
    async def search(self: SsdpDiscovery, listen_s: float) -> list[str]:
        return [LOCATION, "http://192.168.2.240:55000/dmr/ddd2.xml", "http://192.168.2.5/nas.xml"]

    monkeypatch.setattr(SsdpDiscovery, "_search", search)
    monkeypatch.setattr(
        "gravitas.infrastructure.cast.dlna._route_address", lambda host: "192.168.2.10"
    )
    respx.get(LOCATION).mock(return_value=httpx.Response(200, text=PANASONIC))
    respx.get("http://192.168.2.240:55000/dmr/ddd2.xml").mock(
        return_value=httpx.Response(200, text=PANASONIC)
    )
    respx.get("http://192.168.2.5/nas.xml").mock(
        return_value=httpx.Response(200, text=PANASONIC.replace("MediaRenderer", "MediaServer"))
    )
    found = await SsdpDiscovery().discover(0.1)
    assert [(t.name, t.local_address) for t in found] == [("40CS630_Series", "192.168.2.10")]
