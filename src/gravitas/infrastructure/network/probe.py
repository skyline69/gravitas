"""One-shot bandwidth measurement against a stream's own host.

Used exactly once per transport bucket: when the user turns the feature on and
nothing has ever been measured on this link, the Sources list would otherwise
be sorted by nothing at all until the first playback ends. So the first source
the user is about to see is asked for its first few megabytes, timed, and
dropped.

The host is the one the stream would come from anyway, so this adds no new
party to the transaction and no telemetry endpoint. It is deliberately small
and deadlined: the measurement is worth a few megabytes once, never a stall in
front of a list the user is waiting on.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator, Sequence
from typing import cast

import httpx

from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)

# How much to pull. Big enough to outrun TCP slow-start on a fast link (a
# 300 Mbps line moves this in ~80ms), small enough to be a rounding error
# against the episode that follows.
PROBE_BYTES = 3 * 1024 * 1024
# Hard deadline. A slow link will not finish PROBE_BYTES in time, and that is
# fine -- whatever arrived within the deadline is itself the measurement.
PROBE_TIMEOUT_S = 2.0
# Below this, timing says more about latency and slow-start than throughput.
_MIN_SAMPLE_BYTES = 256 * 1024
_MIN_ELAPSED_S = 0.05


class HttpBandwidthProbe:
    """BandwidthProbe over the app's shared httpx client."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def measure_kbps(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> int | None:
        request_headers = {key: value for key, value in headers}
        # Range, so a host that honours it hands back exactly the slice wanted.
        # A host that ignores it answers 200 with the whole file instead, which
        # is why the read loop below stops counting at PROBE_BYTES rather than
        # trusting the response length.
        request_headers["Range"] = f"bytes=0-{PROBE_BYTES - 1}"
        received = 0
        started = time.monotonic()
        try:
            async with self._client.stream(
                "GET",
                url,
                headers=request_headers,
                timeout=PROBE_TIMEOUT_S,
                follow_redirects=True,
            ) as response:
                if response.status_code >= 400:
                    _log.debug(
                        "bandwidth probe refused by %s (HTTP %d)",
                        abbreviate_url(url),
                        response.status_code,
                    )
                    return None
                # The iterator is held and closed by hand because this loop
                # ALWAYS leaves early -- that is the whole point of a probe.
                # Breaking out of `async for response.aiter_bytes()` strands
                # httpcore's generator mid-yield, and Python finalises it
                # whenever the GC gets there, which is typically after the
                # qasync loop has stopped: "async generator ignored
                # GeneratorExit", then "RuntimeError: no running event loop",
                # printed from a stack that has nothing to do with whatever
                # the app is doing at the time.
                # httpx types this as AsyncIterator, which has no aclose();
                # what it returns is an async generator, and closing it is the
                # entire point here.
                body = cast(AsyncGenerator[bytes, None], response.aiter_bytes())
                try:
                    async for chunk in body:
                        # Clamped: a host that ignores Range can hand over a
                        # chunk larger than was asked for, and counting bytes
                        # the probe did not ask for would report a rate the
                        # link never had. Clamping attributes the whole
                        # elapsed time to PROBE_BYTES, which reads low -- the
                        # safe direction.
                        received = min(received + len(chunk), PROBE_BYTES)
                        elapsed = time.monotonic() - started
                        if received >= PROBE_BYTES or elapsed >= PROBE_TIMEOUT_S:
                            break
                finally:
                    await body.aclose()
        except Exception as exc:
            # Every failure here means the same thing to the caller -- no
            # measurement -- and none of them is worth a toast: the user asked
            # for a sorted list, not for a network diagnostic.
            _log.debug("bandwidth probe failed for %s: %s", abbreviate_url(url), exc)
            return None
        elapsed = time.monotonic() - started
        if received < _MIN_SAMPLE_BYTES or elapsed < _MIN_ELAPSED_S:
            _log.debug("bandwidth probe inconclusive (%d bytes in %.3fs)", received, elapsed)
            return None
        kbps = int(received * 8 / elapsed / 1000)
        _log.info("bandwidth probe: %d kbps (%d bytes in %.2fs)", kbps, received, elapsed)
        return kbps
