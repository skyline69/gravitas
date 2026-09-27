"""The one-shot cold-start bandwidth probe."""

import httpx
import pytest
import respx

from gravitas.infrastructure.network import probe as probe_module
from gravitas.infrastructure.network.probe import PROBE_BYTES, HttpBandwidthProbe

URL = "https://host.example/video.mkv"


class FakeClock:
    """Wall time the probe reads, advanced by hand: a mocked transport returns
    instantly, and a measurement that takes 0 seconds is not a measurement."""

    def __init__(self, step: float) -> None:
        self._now = 0.0
        self._step = step

    def monotonic(self) -> float:
        now = self._now
        self._now += self._step
        return now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    def _install(total_s: float) -> FakeClock:
        # Two reads per call path (start, then each check), so half each.
        fake = FakeClock(total_s / 2)
        monkeypatch.setattr(probe_module, "time", fake)
        return fake

    return _install


@respx.mock
async def test_measures_a_range_response(clock) -> None:
    clock(0.5)
    route = respx.get(URL).mock(return_value=httpx.Response(206, content=b"x" * PROBE_BYTES))
    async with httpx.AsyncClient() as http:
        kbps = await HttpBandwidthProbe(http).measure_kbps(URL)
    # 3 MiB in 0.5s.
    assert kbps == int(PROBE_BYTES * 8 / 0.5 / 1000)
    assert route.calls[0].request.headers["Range"] == f"bytes=0-{PROBE_BYTES - 1}"


@respx.mock
async def test_sends_the_streams_own_proxy_headers(clock) -> None:
    clock(0.5)
    route = respx.get(URL).mock(return_value=httpx.Response(206, content=b"x" * PROBE_BYTES))
    async with httpx.AsyncClient() as http:
        await HttpBandwidthProbe(http).measure_kbps(URL, (("Referer", "https://addon"),))
    assert route.calls[0].request.headers["Referer"] == "https://addon"


@respx.mock
async def test_a_host_ignoring_range_is_still_measured_and_still_bounded(clock) -> None:
    clock(0.5)
    # 200 with the whole file: the probe counts what it read, not what was offered.
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * (PROBE_BYTES * 3)))
    async with httpx.AsyncClient() as http:
        kbps = await HttpBandwidthProbe(http).measure_kbps(URL)
    # Measured over PROBE_BYTES, not over the 9 MiB the host actually sent:
    # counting the extra would claim a rate the link was never asked for.
    assert kbps == int(PROBE_BYTES * 8 / 0.5 / 1000)


@respx.mock
async def test_a_refusal_is_no_measurement(clock) -> None:
    clock(0.5)
    respx.get(URL).mock(return_value=httpx.Response(403))
    async with httpx.AsyncClient() as http:
        assert await HttpBandwidthProbe(http).measure_kbps(URL) is None


@respx.mock
async def test_a_transport_failure_is_no_measurement(clock) -> None:
    clock(0.5)
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        assert await HttpBandwidthProbe(http).measure_kbps(URL) is None


@respx.mock
async def test_too_little_data_is_no_measurement(clock) -> None:
    clock(0.5)
    respx.get(URL).mock(return_value=httpx.Response(206, content=b"x" * 1024))
    async with httpx.AsyncClient() as http:
        assert await HttpBandwidthProbe(http).measure_kbps(URL) is None
