import asyncio
from collections.abc import Sequence

import pytest

from gravitas.application.cast_session import CastSession, cast_mime, uncastable_reason
from gravitas.domain.errors import CastFailed
from gravitas.domain.models import CastStatus


class FakeTarget:
    """A TV that reports whatever the test puts in `reports`, last one held."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.reports: list[CastStatus | CastFailed] = [CastStatus("loading")]
        self.refuse_play = False

    @property
    def name(self) -> str:
        return "Living room"

    @property
    def local_address(self) -> str:
        return "192.168.2.10"

    async def play(self, url: str, *, mime: str, title: str) -> None:
        self.calls.append(f"play:{url}:{mime}:{title}")
        if self.refuse_play:
            raise CastFailed("Living room refused SetAVTransportURI (UPnP error 714).")

    async def pause(self) -> None:
        self.calls.append("pause")

    async def resume(self) -> None:
        self.calls.append("resume")

    async def seek(self, seconds: float) -> None:
        self.calls.append(f"seek:{seconds:.0f}")

    async def stop(self) -> None:
        self.calls.append("stop")

    async def status(self) -> CastStatus:
        report = self.reports[0] if len(self.reports) == 1 else self.reports.pop(0)
        if isinstance(report, CastFailed):
            raise report
        return report

    async def volume(self) -> int:
        return 20

    async def set_volume(self, volume: int) -> None:
        self.calls.append(f"volume:{volume}")

    async def set_muted(self, muted: bool) -> None:
        self.calls.append(f"muted:{muted}")


class FakeRelay:
    def __init__(self) -> None:
        self.started = False
        self.closed = False
        self.registered: list[tuple[str, tuple[tuple[str, str], ...], str | None]] = []

    async def start(self) -> None:
        self.started = True

    def local_url(
        self,
        url: str,
        headers: Sequence[tuple[str, str]] = (),
        *,
        content_type: str | None = None,
    ) -> str:
        self.registered.append((url, tuple(headers), content_type))
        return "http://192.168.2.10:5000/tok/video.mkv"

    async def aclose(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Harness:
    def __init__(self) -> None:
        self.target = FakeTarget()
        self.relay = FakeRelay()
        self.clock = Clock()
        self.ended = 0
        self.failures: list[str] = []
        self.errors: list[str] = []
        self.session = CastSession(
            self.target,
            self.relay,
            on_changed=lambda: None,
            on_ended=self._ended,
            on_failed=self.failures.append,
            on_error=self.errors.append,
            clock=self.clock,
        )

    def _ended(self) -> None:
        self.ended += 1


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CastSession, "POLL_S", 0.0)


async def settle(times: int = 5) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


def test_the_type_comes_from_the_file_name_before_the_url() -> None:
    assert cast_mime("https://signer/play/abc", "Ted.S01E07.1080p.WEB.mp4") == "video/mp4"
    assert cast_mime("https://cdn/x/Film.2020.webm") == "video/webm"
    # A signing endpoint's path says nothing: debrid files are Matroska.
    assert cast_mime("https://signer/play/abc") == "video/x-matroska"


def test_what_a_tv_cannot_be_sent() -> None:
    assert uncastable_reason("https://cdn/file.mkv") is None
    assert uncastable_reason("https://www.youtube.com/watch?v=x") is not None
    assert uncastable_reason("https://cdn/live/index.m3u8") is not None
    assert uncastable_reason("magnet:?xt=urn:btih:abc") is not None


async def test_the_tv_is_handed_the_relays_url_with_the_type_and_headers() -> None:
    h = Harness()
    await h.session.start()
    await h.session.load(
        "https://signer/x", (("Referer", "https://addon/"),), title="Ted", mime="video/mp4"
    )
    assert h.relay.started
    assert h.relay.registered == [
        ("https://signer/x", (("Referer", "https://addon/"),), "video/mp4")
    ]
    assert h.target.calls == ["play:http://192.168.2.10:5000/tok/video.mkv:video/mp4:Ted"]
    assert h.session.volume == 20.0
    await h.session.close(stop_target=True)


async def test_a_resume_point_is_sought_once_the_tv_plays() -> None:
    h = Harness()
    h.target.reports = [CastStatus("loading"), CastStatus("playing", 0.5, 2530.0)]
    await h.session.load("https://x", (), title="", mime="video/x-matroska", start=231.0)
    # Loading: the resume point stands on the timeline, not the TV's 0.
    assert h.session.position() == 231.0
    await settle()
    assert "seek:231" in h.target.calls
    assert h.session.position() == 231.0
    assert h.session.duration() == 2530.0
    await h.session.close(stop_target=False)


async def test_a_seek_shows_at_once_and_the_tv_confirms_it() -> None:
    h = Harness()
    h.target.reports = [CastStatus("playing", 10.0, 600.0)]
    await h.session.load("https://x", (), title="", mime="video/x-matroska")
    await settle()
    h.session.seek(300.0)
    assert h.session.position() == 300.0 and h.session.loading
    await settle()
    # Still at 10 by the TV's report, within the hold: the target stays up.
    assert h.session.position() == 300.0
    h.target.reports = [CastStatus("playing", 301.0, 600.0)]
    await settle()
    assert h.session.position() == 301.0 and not h.session.loading
    await h.session.close(stop_target=False)


async def test_a_tv_that_stops_after_playing_has_ended_the_playback() -> None:
    h = Harness()
    h.target.reports = [CastStatus("playing", 40.0, 600.0), CastStatus("stopped")]
    await h.session.load("https://x", (), title="", mime="video/x-matroska")
    await settle()
    assert h.ended == 1
    # Where it was playing, not the stopped report's 0.
    assert h.session.position() == 40.0
    await h.session.close(stop_target=False)
    assert h.relay.closed and "stop" not in h.target.calls


async def test_stopped_while_opening_is_patience_then_a_failure() -> None:
    h = Harness()
    h.target.reports = [CastStatus("stopped")]
    await h.session.load("https://x", (), title="", mime="video/x-matroska")
    await settle()
    assert h.failures == [] and h.session.loading
    h.clock.now += CastSession.OPEN_GRACE_S + 1
    await settle()
    assert h.failures == [CastSession.NEVER_PLAYED]
    await h.session.close(stop_target=False)


async def test_a_tv_that_goes_quiet_is_lost() -> None:
    h = Harness()
    h.target.reports = [CastFailed("did not answer")]
    await h.session.load("https://x", (), title="", mime="video/x-matroska")
    await settle(CastSession.LOST_AFTER_POLLS * 3)
    assert h.failures == [CastSession.LOST]
    await h.session.close(stop_target=False)


async def test_a_refused_load_raises() -> None:
    h = Harness()
    h.target.refuse_play = True
    with pytest.raises(CastFailed):
        await h.session.load("https://x", (), title="", mime="video/x-matroska")


async def test_a_failed_command_is_reported_not_raised() -> None:
    h = Harness()
    h.target.reports = [CastStatus("playing", 10.0, 600.0)]
    await h.session.load("https://x", (), title="", mime="video/x-matroska")

    async def refuse() -> None:
        raise CastFailed("Living room refused Pause (UPnP error 701).")

    h.target.pause = refuse  # type: ignore[method-assign]
    h.session.pause()
    assert h.session.paused  # shown at once
    await settle()
    assert h.errors == ["Living room refused Pause (UPnP error 701)."]
    # ...and the next report puts the truth back.
    assert not h.session.paused
    await h.session.close(stop_target=True)
    assert h.target.calls[-1] == "stop"
