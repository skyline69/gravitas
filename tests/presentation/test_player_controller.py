import threading
from collections.abc import Callable, Sequence

import pytest

from gravitas.domain.models import DecodeReport, MediaFormat
from gravitas.presentation.controllers.player_controller import PlayerController


class FakePlayer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.sub: int | None = -1
        self.aud: int | None = -1
        self.prepared = 0
        self._paused = False
        self._muted = False
        self.loading = False
        self.buffered = 0.0
        self.speed = 0.0
        self.dv_profile = 0
        self.decode = DecodeReport()
        self.size = (0, 0)
        self._volume = 100.0
        self._position = 0.0
        self.start = 0.0
        self.headers: tuple[tuple[str, str], ...] = ()
        self._duration = 100.0
        self._tracks: list[tuple[int, str]] = [(2, "English")]
        self._audio: list[tuple[int, str]] = [(1, "eng · 5.1")]
        self._tracks_changed_callback: Callable[[], None] | None = None
        self._state_changed_callback: Callable[[], None] | None = None
        self._stream_ended_callback: Callable[[], None] | None = None
        self._load_failed_callback: Callable[[], None] | None = None

    def play(
        self,
        url: str,
        *,
        start: float = 0.0,
        headers: Sequence[tuple[str, str]] = (),
        upstream_cached: bool = False,
    ) -> None:
        if self.fail:
            from gravitas.domain.errors import PlaybackFailed

            raise PlaybackFailed("boom")
        self.start = start
        self.headers = tuple(headers)
        self.upstream_cached = upstream_cached
        self._position = start
        # Recorded without `start` so the existing call-order assertions hold.
        self.calls.append(f"play:{url}")

    def stop(self) -> None:
        self.calls.append("stop")

    def pause(self) -> None:
        self._paused = True
        self.calls.append("pause")

    def resume(self) -> None:
        self._paused = False
        self.calls.append("resume")

    def is_paused(self) -> bool:
        return self._paused

    def seek(self, seconds: float) -> None:
        self._position = seconds
        self.calls.append(f"seek:{seconds}")

    def position(self) -> float:
        return self._position

    def duration(self) -> float:
        return self._duration

    def media_format(self) -> MediaFormat:
        return getattr(self, "_format", MediaFormat())

    def set_volume(self, volume: float) -> None:
        self._volume = volume

    def volume(self) -> float:
        return self._volume

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def is_muted(self) -> bool:
        return self._muted

    def is_loading(self) -> bool:
        return self.loading

    def buffered_to(self) -> float:
        return self.buffered

    def download_speed(self) -> float:
        return self.speed

    def video_dolby_vision_profile(self) -> int:
        return self.dv_profile

    def chapters(self) -> list[tuple[float, str]]:
        return list(getattr(self, "chapter_list", []))

    def choose_fallback_subtitle(self) -> bool:
        self.calls.append("fallback-subtitle")
        return False

    def has_preferred_subtitle(self) -> bool:
        # What the track list says; tests that only set a selection mean
        # "the file has one" by it.
        known = getattr(self, "file_has_preferred", None)
        return self.current_subtitle_track() is not None if known is None else bool(known)

    def set_subtitle_delay(self, seconds: float) -> None:
        self.delay = seconds

    def subtitle_delay(self) -> float:
        return getattr(self, "delay", 0.0)

    def add_subtitle(self, url: str, title: str, lang: str, *, select: bool = True) -> None:
        self.added = [*getattr(self, "added", []), (url, title, lang, select)]
        if select:
            self.sub = 900 + len(self.added)

    def decode_report(self) -> DecodeReport:
        return self.decode

    def video_size(self) -> tuple[int, int]:
        return self.size

    def prepare_track_switching(self) -> None:
        self.prepared += 1

    def set_subtitle_track(self, track_id: int | None) -> None:
        self.sub = track_id

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return self._tracks

    def set_audio_track(self, track_id: int | None) -> None:
        self.aud = track_id

    def audio_tracks(self) -> list[tuple[int, str]]:
        return self._audio

    def current_subtitle_track(self) -> int | None:
        return self.sub if isinstance(self.sub, int) and self.sub >= 0 else None

    def current_audio_track(self) -> int | None:
        return self.aud if isinstance(self.aud, int) and self.aud >= 0 else None

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._tracks_changed_callback = callback

    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._state_changed_callback = callback

    def set_stream_ended_callback(self, callback: Callable[[], None] | None) -> None:
        self._stream_ended_callback = callback

    def set_load_failed_callback(self, callback: Callable[[], None] | None) -> None:
        self._load_failed_callback = callback

    def set_opened_callback(self, callback: Callable[[], None] | None) -> None:
        self._opened_callback = callback

    def trigger_opened(self) -> None:
        if getattr(self, "_opened_callback", None) is not None:
            self._opened_callback()

    def render_handle(self) -> object | None:
        return None

    def apply_subtitle_style(self, style: object) -> None:
        self.calls.append(f"style:{style}")

    def set_track_languages(self, languages: object) -> None:
        self.calls.append(f"languages:{languages}")

    def trigger_tracks_changed(self, tracks: list[tuple[int, str]] | None = None) -> None:
        if tracks is not None:
            self._tracks = tracks
        if self._tracks_changed_callback is not None:
            self._tracks_changed_callback()

    def trigger_state_changed(self) -> None:
        if self._state_changed_callback is not None:
            self._state_changed_callback()

    def trigger_stream_ended(self) -> None:
        # mpv holds the last frame paused at end-of-file (keep-open), whether
        # the file ended or the socket did.
        self._paused = True
        if self._stream_ended_callback is not None:
            self._stream_ended_callback()

    def trigger_load_failed(self) -> None:
        # A URL that never opened: mpv reports the END_FILE error and goes
        # idle. Nothing is paused and no end of file is ever reached, which is
        # exactly why this is a separate signal.
        if self._load_failed_callback is not None:
            self._load_failed_callback()

    def shutdown(self) -> None:
        self.calls.append("shutdown")


def test_play_and_controls(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.pause()
    controller.resume()
    controller.seek(30.0)
    assert player.calls == ["play:http://s/v.mkv", "pause", "resume", "seek:30.0"]


def test_toggle_pause(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.togglePause()
    assert controller.paused is True
    controller.togglePause()
    assert controller.paused is False


def test_seek_by_clamps_to_bounds(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.seekBy(-10.0)
    assert player.position() == 0.0
    player.seek(95.0)
    controller.seekBy(10.0)
    assert player.position() == 100.0  # clamped to duration


def test_volume_and_mute(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.setVolume(40.0)
    assert controller.volume == 40.0
    controller.toggleMute()
    assert controller.muted is True
    controller.toggleMute()
    assert controller.muted is False


def test_stop_forwards(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.stop()
    assert "stop" in player.calls


def test_subtitle_tracks_exposed_as_dicts(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    assert controller.subtitleTracks() == []
    controller.play("http://s/v.mkv")
    assert controller.subtitleTracks() == [{"id": 2, "title": "English"}]


def test_audio_tracks_exposed_as_dicts(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    assert controller.audioTracks() == [{"id": 1, "title": "eng · 5.1"}]
    controller.selectAudio(1)
    assert player.aud == 1
    controller.selectAudio(-1)
    assert player.aud is None


def test_current_tracks_reported(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    player.sub = 2
    player.aud = 1
    assert controller.currentSubtitle() == 2
    assert controller.currentAudio() == 1
    controller.selectSubtitle(-1)  # off
    assert controller.currentSubtitle() == -1


def test_select_subtitle(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.selectSubtitle(2)
    assert player.sub == 2


def test_playback_error_emits_signal(qapp: object) -> None:
    player = FakePlayer(fail=True)
    controller = PlayerController(lambda: player)
    received: list[str] = []
    controller.errorOccurred.connect(received.append)
    controller.play("http://s/v.mkv")
    assert received == ["boom"]


def test_subtitle_tracks_changed_signal_refreshes_tracks(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")

    fired: list[None] = []
    controller.subtitleTracksChanged.connect(lambda: fired.append(None))

    player.trigger_tracks_changed([(5, "French"), (6, "German")])

    assert fired == [None]
    assert controller.subtitleTracks() == [
        {"id": 5, "title": "French"},
        {"id": 6, "title": "German"},
    ]


def test_is_loading_polled(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    assert controller.isLoading() is False
    controller.play("http://s/v.mkv")
    player.loading = True
    assert controller.isLoading() is True


def test_buffered_to_polled(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    assert controller.bufferedTo() == 0.0  # no player yet
    controller.play("http://s/v.mkv")
    player.buffered = 321.5
    assert controller.bufferedTo() == 321.5


def test_state_callback_emits_state_changed(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")

    fired: list[None] = []
    controller.stateChanged.connect(lambda: fired.append(None))
    player.trigger_state_changed()
    assert fired == [None]


def test_subtitle_style_applied_on_creation_and_on_demand(qapp: object) -> None:
    from gravitas.domain.models import SubtitleStyle

    player = FakePlayer()
    style = SubtitleStyle(font_size=70)
    controller = PlayerController(lambda: player, lambda: style)
    controller.play("http://s/v.mkv")
    assert any(call.startswith("style:") for call in player.calls)
    before = len(player.calls)
    controller.applySubtitleStyle()
    assert len(player.calls) == before + 1


CONTEXT = {
    "mediaId": "tt9",
    "videoId": "tt9:1:1",
    "type": "series",
    "name": "The Show",
    "poster": "http://p/9.jpg",
    "label": "S1E1 · Pilot",
}


class FakeProgress:
    def __init__(self, resume: float = 0.0) -> None:
        self._resume = resume
        self.records: list[dict[str, object]] = []

    def resume_position(self, media_id: str, video_id: str = "") -> float:
        return self._resume

    def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


def test_play_resumes_from_saved_position(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress(resume=1820.5)
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert player.start == 1820.5


def test_play_defers_resumed_past_the_current_event_loop_turn(qapp: object) -> None:
    """StackView applies `url` (which triggers play()) between beginCreate()
    and completeCreate(); Player.qml's Connections only goes live in
    completeCreate(). A synchronous emit fires into the void, so `resumed`
    must not be observable until the event loop turns at least once."""
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress(resume=90.0))
    seen: list[float] = []
    controller.resumed.connect(seen.append)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert seen == []  # not yet — still the same turn a QQC Connections would miss
    qapp.processEvents()
    assert seen == [90.0]


def test_play_emits_resumed_only_when_resuming(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress(resume=90.0))
    seen: list[float] = []
    controller.resumed.connect(seen.append)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    qapp.processEvents()
    assert seen == [90.0]


def test_play_from_scratch_does_not_emit_resumed(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress(resume=0.0))
    seen: list[float] = []
    controller.resumed.connect(seen.append)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert seen == []
    assert player.start == 0.0


def test_media_title_and_label_follow_context(qapp: object) -> None:
    controller = PlayerController(lambda: FakePlayer())
    changes: list[bool] = []
    controller.mediaContextChanged.connect(lambda: changes.append(True))
    controller.setMediaContext(CONTEXT)
    assert controller.mediaTitle == CONTEXT["name"]
    assert controller.mediaLabel == CONTEXT["label"]
    controller.setMediaContext({})
    assert controller.mediaTitle == ""
    assert controller.mediaLabel == ""
    assert len(changes) == 2


def test_cleared_context_neither_resumes_nor_records(qapp: object) -> None:
    """The trailer path: playback of something that is NOT the title. After
    setMediaContext({}) the previous title's saved position must not leak in
    as a resume, and nothing may be recorded over that title's progress."""
    player = FakePlayer()
    progress = FakeProgress(resume=588.0)
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.setMediaContext({})
    seen: list[float] = []
    controller.resumed.connect(seen.append)
    controller.play("http://yt/trailer")
    qapp.processEvents()
    assert player.start == 0.0
    assert seen == []
    controller.flushProgress()
    assert progress.records == []


def test_records_context_on_flush(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    controller.flushProgress()
    assert progress.records == [
        {
            "media_id": "tt9",
            "video_id": "tt9:1:1",
            "type": "series",
            "name": "The Show",
            "poster": "http://p/9.jpg",
            "label": "S1E1 · Pilot",
            "position": 300.0,
            "duration": 100.0,
        }
    ]


def test_no_context_means_no_record(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.play("http://s/v.mkv")  # played from an unknown surface
    player.seek(300.0)
    controller.flushProgress()
    assert progress.records == []  # a nameless row helps nobody


def test_unknown_duration_means_no_record(qapp: object) -> None:
    player = FakePlayer()
    player._duration = 0.0  # mpv has not parsed the file yet
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.flushProgress()
    assert progress.records == []


def test_pause_records(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    controller.pause()
    assert len(progress.records) == 1


def test_stop_records_and_halts_the_timer(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert controller.is_recording() is True
    player.seek(300.0)
    controller.stop()
    assert len(progress.records) == 1
    # A timer left running would keep writing after playback ended.
    assert controller.is_recording() is False


def test_empty_poster_recorded_as_none(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext({**CONTEXT, "poster": ""})
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    controller.flushProgress()
    assert progress.records[0]["poster"] is None


def test_timer_tick_skips_while_paused(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    player._paused = True
    controller._on_tick()
    assert progress.records == []


def test_works_without_a_progress_repo(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)  # progress disabled
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.flushProgress()
    assert player.start == 0.0


def test_switching_titles_without_stopping_records_against_the_right_one(
    qapp: object,
) -> None:
    """Playing again without an intervening stop() -- picking another title from
    Continue Watching, say. The autosave timer simply restarts, but the context
    must follow, or the new title's position is filed under the old one."""
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)

    controller.setMediaContext(CONTEXT)
    controller.play("http://s/first.mkv")
    player.seek(300.0)
    controller.flushProgress()

    controller.setMediaContext({**CONTEXT, "mediaId": "tt2", "videoId": "", "name": "Other"})
    controller.play("http://s/second.mkv")
    player.seek(400.0)
    controller.flushProgress()

    assert controller.is_recording() is True
    assert [(r["media_id"], r["position"]) for r in progress.records] == [
        ("tt9", 300.0),
        ("tt2", 400.0),
    ]


def test_scrobble_events_follow_the_playback_lifecycle(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress())
    events: list[tuple[str, float]] = []
    controller.scrobbleEvent.connect(
        lambda action, _ctx, position, _duration: events.append((action, position))
    )
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.pause()
    controller.resume()
    player.seek(300.0)
    controller.stop()
    assert events == [("start", 0.0), ("pause", 0.0), ("start", 0.0), ("stop", 300.0)]


def test_toggle_pause_emits_scrobble_events(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress())
    events: list[str] = []
    controller.scrobbleEvent.connect(lambda action, _c, _p, _d: events.append(action))
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.togglePause()
    controller.togglePause()
    assert events == ["start", "pause", "start"]


def test_no_scrobble_event_without_media_context(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    events: list[str] = []
    controller.scrobbleEvent.connect(lambda action, _c, _p, _d: events.append(action))
    controller.play("http://s/v.mkv")  # unknown surface: nothing to scrobble
    controller.stop()
    assert events == []


def test_scrobble_context_snapshot_is_detached(qapp: object) -> None:
    """The consumer runs async; by then setMediaContext may have replaced the
    dict. The emitted copy must not follow it."""
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress())
    contexts: list[dict] = []
    controller.scrobbleEvent.connect(lambda _a, ctx, _p, _d: contexts.append(ctx))
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.setMediaContext({**CONTEXT, "mediaId": "tt-other"})
    assert contexts[0]["mediaId"] == "tt9"


def test_play_forwards_proxy_headers_from_qml() -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("https://cdn/v.mp4", {"Referer": "https://origin/"})
    assert player.headers == (("Referer", "https://origin/"),)


def test_play_without_headers_passes_none_through() -> None:
    # QML's single-argument overload must keep working.
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("https://cdn/v.mp4")
    assert player.headers == ()


# --- stall recovery ---------------------------------------------------------
#
# A stream that dies mid-episode arrives as end-of-file, not as an error: mpv
# holds the last frame paused and playback just stops.


def _stalled(delays: tuple[int, ...] = (0, 0, 0)) -> tuple[PlayerController, FakePlayer]:
    """A controller whose stream died 600s into a 2600s episode."""
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    # It played for ten minutes, so the player opened it: what separates a
    # drop from a source that never opened.
    player.trigger_opened()
    controller.RECONNECT_DELAYS_MS = delays  # type: ignore[misc]
    player._position = 600.0
    player._duration = 2600.0
    controller.position()  # the QML poll that records where playback is
    player.trigger_stream_ended()
    return controller, player


def test_a_dropped_stream_is_not_mistaken_for_the_end(qapp: object) -> None:
    controller, _player = _stalled()
    assert controller.reconnecting is True
    # The spinner is driven by this: without it the player just sits paused
    # with no sign that anything is wrong.
    assert controller.isLoading() is True


def test_reconnect_reloads_the_same_url_at_the_stall_position(qapp: object) -> None:
    from PySide6.QtTest import QTest

    _controller, player = _stalled()
    QTest.qWait(30)
    assert player.calls[-1] == "play:http://s/v.mkv"
    assert player.start == 600.0


def test_reconnect_keeps_the_streams_proxy_headers(qapp: object) -> None:
    from PySide6.QtTest import QTest

    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("https://cdn/v.mp4", {"Referer": "https://origin/"})
    controller.RECONNECT_DELAYS_MS = (0,)  # type: ignore[misc]
    player._position = 300.0
    player._duration = 2600.0
    controller.position()
    player.headers = ()
    player.trigger_stream_ended()
    QTest.qWait(30)
    # Dropping them here would 403 on exactly the addons that need them.
    assert player.headers == (("Referer", "https://origin/"),)


def test_recovery_ends_once_the_playhead_moves_again(qapp: object) -> None:
    from PySide6.QtTest import QTest

    controller, player = _stalled()
    QTest.qWait(30)
    player._position = 601.0
    controller.position()
    assert controller.reconnecting is False
    assert controller.isLoading() is False


def test_a_finished_episode_is_not_reconnected(qapp: object) -> None:
    from PySide6.QtTest import QTest

    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    player._position = 2599.0
    player._duration = 2600.0
    controller.position()
    before = list(player.calls)
    player.trigger_stream_ended()
    QTest.qWait(30)
    assert controller.reconnecting is False
    assert player.calls == before


def test_a_stall_while_paused_waits_for_play(qapp: object) -> None:
    from PySide6.QtTest import QTest

    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.RECONNECT_DELAYS_MS = (0,)  # type: ignore[misc]
    player._position = 600.0
    player._duration = 2600.0
    controller.position()
    controller.pause()
    player.trigger_stream_ended()
    QTest.qWait(30)
    # Reloading here would start playing under someone who chose to stop.
    assert controller.reconnecting is False
    assert player.calls[-1] != "play:http://s/v.mkv"

    controller.togglePause()
    QTest.qWait(30)
    assert player.calls[-1] == "play:http://s/v.mkv"
    assert player.start == 600.0


def test_stopping_disarms_recovery(qapp: object) -> None:
    from PySide6.QtTest import QTest

    controller, player = _stalled()
    controller.stop()
    before = list(player.calls)
    QTest.qWait(30)
    assert controller.reconnecting is False
    assert player.calls == before


def test_recovery_gives_up_and_reports_it(qapp: object) -> None:
    from PySide6.QtTest import QTest

    controller, player = _stalled()
    controller.MAX_RECONNECT_ATTEMPTS = 2  # type: ignore[misc]
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    # The stall armed the first attempt; two more end-of-files exhaust them.
    for _ in range(2):
        QTest.qWait(20)
        player._duration = 0.0  # a reload that never opened the file
        player.trigger_stream_ended()
    QTest.qWait(20)
    assert controller.reconnecting is False
    assert errors and "connection" in errors[0].lower()


def test_video_aspect_reports_decoded_shape(qapp: object) -> None:
    player = FakePlayer()
    player.size = (1920, 1080)
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    assert controller.videoAspect == pytest.approx(1920 / 1080)


def test_video_aspect_falls_back_to_16_9_before_first_frame(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    # mpv reports nothing until a frame decodes; the tile still needs a shape.
    assert controller.videoAspect == pytest.approx(16 / 9)


def test_video_aspect_falls_back_without_a_player(qapp: object) -> None:
    controller = PlayerController(lambda: FakePlayer())
    assert controller.videoAspect == pytest.approx(16 / 9)


def test_resize_suspend_pauses_and_resumes(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.suspendForResize()
    assert player.is_paused() is True
    controller.resumeAfterResize()
    assert player.is_paused() is False


def test_resize_suspend_is_idempotent(qapp: object) -> None:
    # Every geometry change of a drag calls it; only the first may act.
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    for _ in range(20):
        controller.suspendForResize()
    controller.resumeAfterResize()
    assert player.is_paused() is False
    assert player.calls.count("pause") == 1


def test_resize_never_resumes_a_video_the_viewer_paused(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.pause()
    controller.suspendForResize()
    controller.resumeAfterResize()
    assert player.is_paused() is True


def test_pressing_play_during_a_resize_wins(qapp: object) -> None:
    # The viewer hits Space mid-drag: the settle must not undo it.
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller.suspendForResize()
    controller.togglePause()  # deliberate: play on
    assert player.is_paused() is False
    controller.resumeAfterResize()
    assert player.is_paused() is False

    # ...and the rest of the drag must not take it back. Each geometry change
    # calls suspendForResize again; after a deliberate press they are inert.
    controller.suspendForResize()
    controller.togglePause()  # the viewer plays on mid-drag
    assert player.is_paused() is False
    for _ in range(10):
        controller.suspendForResize()
    assert player.is_paused() is False
    controller.resumeAfterResize()
    assert player.is_paused() is False

    # The override lasts for that drag only; the next one pauses again.
    controller.suspendForResize()
    assert player.is_paused() is True


def test_resize_suspend_tells_trakt_nothing(qapp: object) -> None:
    """A scrobbled pause/start per drag would flood the account with a
    playback history nobody performed."""
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    events: list[str] = []
    controller.scrobbleEvent.connect(lambda action, *_: events.append(action))
    controller.suspendForResize()
    controller.resumeAfterResize()
    assert events == []


def test_resize_suspend_leaves_the_viewers_intent_alone(qapp: object) -> None:
    """_user_paused drives whether stall recovery restarts playback by itself;
    a resize must not be mistaken for the viewer pausing."""
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    assert controller._user_paused is False
    controller.suspendForResize()
    assert controller._user_paused is False
    controller.resumeAfterResize()
    assert controller._user_paused is False


def test_resize_suspend_without_a_player_is_inert(qapp: object) -> None:
    controller = PlayerController(lambda: FakePlayer())
    controller.suspendForResize()
    controller.resumeAfterResize()


class RecordingSink:
    def __init__(self) -> None:
        self.samples: list[int] = []

    def record(self, kbps: int) -> None:
        self.samples.append(kbps)


def test_tick_samples_the_players_read_speed(qapp: object) -> None:
    player = FakePlayer()
    sink = RecordingSink()
    controller = PlayerController(lambda: player, speed_sink=sink)
    controller.play("http://s/v.mkv")
    player.speed = 3_750_000.0  # bytes/s == 30 Mbps
    controller._on_tick()
    assert sink.samples == [30_000]


def test_a_paused_player_is_not_a_measurement(qapp: object) -> None:
    player = FakePlayer()
    sink = RecordingSink()
    controller = PlayerController(lambda: player, speed_sink=sink)
    controller.play("http://s/v.mkv")
    player.speed = 3_750_000.0
    controller.pause()
    controller._on_tick()
    assert sink.samples == []


def test_a_local_file_reports_no_speed_and_is_skipped(qapp: object) -> None:
    player = FakePlayer()
    sink = RecordingSink()
    controller = PlayerController(lambda: player, speed_sink=sink)
    controller.play("/home/me/film.mkv")
    player.speed = 0.0
    controller._on_tick()
    assert sink.samples == []


def test_samples_are_written_once_at_the_end_not_every_tick(qapp: object) -> None:
    player = FakePlayer()
    writes: list[int] = []
    controller = PlayerController(
        lambda: player,
        speed_sink=RecordingSink(),
        speed_persist=lambda: writes.append(1),
    )
    controller.play("http://s/v.mkv")
    player.speed = 3_750_000.0
    controller._on_tick()
    controller._on_tick()
    assert writes == []  # a film must not rewrite settings every 5 seconds
    controller.stop()
    assert writes == [1]


def test_quitting_mid_playback_still_writes_the_samples(qapp: object) -> None:
    player = FakePlayer()
    writes: list[int] = []
    controller = PlayerController(
        lambda: player,
        speed_sink=RecordingSink(),
        speed_persist=lambda: writes.append(1),
    )
    controller.play("http://s/v.mkv")
    player.speed = 3_750_000.0
    controller._on_tick()
    controller.flushProgress()  # wired to aboutToQuit
    assert writes == [1]


def test_nothing_is_written_when_nothing_was_sampled(qapp: object) -> None:
    player = FakePlayer()
    writes: list[int] = []
    controller = PlayerController(
        lambda: player,
        speed_sink=RecordingSink(),
        speed_persist=lambda: writes.append(1),
    )
    controller.play("/home/me/film.mkv")
    controller.stop()
    assert writes == []


def test_dolby_vision_profile_5_warns_once(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    warnings: list[str] = []
    controller.playbackWarning.connect(warnings.append)
    controller.play("http://s/dv5.mp4")
    player.dv_profile = 5

    player.trigger_tracks_changed()
    player.trigger_tracks_changed()  # mpv republishes the list constantly

    assert len(warnings) == 1
    assert "profile 5" in warnings[0]


def test_dolby_vision_profile_8_is_not_warned_about(qapp: object) -> None:
    # Profile 8 has an HDR10 base layer and renders correctly.
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    warnings: list[str] = []
    controller.playbackWarning.connect(warnings.append)
    controller.play("http://s/dv8.mp4")
    player.dv_profile = 8

    player.trigger_tracks_changed()

    assert warnings == []


def test_the_dv_warning_rearms_for_the_next_source(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    warnings: list[str] = []
    controller.playbackWarning.connect(warnings.append)
    player.dv_profile = 5

    controller.play("http://s/one.mp4")
    player.trigger_tracks_changed()
    controller.play("http://s/two.mp4")
    player.trigger_tracks_changed()

    assert len(warnings) == 2


class LearningSink:
    def __init__(self, already: set[str] | None = None) -> None:
        self.seen: list[tuple[str, str]] = []
        self._already = already or set()

    def remember(self, name: str, title: str) -> bool:
        self.seen.append((name, title))
        if name in self._already:
            return False
        self._already.add(name)
        return True


def test_a_profile_5_source_is_remembered_and_persisted(qapp: object) -> None:
    player = FakePlayer()
    sink = LearningSink()
    writes: list[int] = []
    controller = PlayerController(
        lambda: player, incompatible_sink=sink, speed_persist=lambda: writes.append(1)
    )
    controller.setSourceLabel("Silo S01E01 DV", "web")
    controller.play("http://s/dv5.mp4")
    player.dv_profile = 5

    player.trigger_tracks_changed()

    assert sink.seen == [("Silo S01E01 DV", "web")]
    assert writes == [1]


def test_a_source_already_known_is_not_written_again(qapp: object) -> None:
    player = FakePlayer()
    sink = LearningSink(already={"Silo S01E01 DV"})
    writes: list[int] = []
    controller = PlayerController(
        lambda: player, incompatible_sink=sink, speed_persist=lambda: writes.append(1)
    )
    controller.setSourceLabel("Silo S01E01 DV", "web")
    controller.play("http://s/dv5.mp4")
    player.dv_profile = 5

    player.trigger_tracks_changed()

    assert sink.seen == [("Silo S01E01 DV", "web")]
    assert writes == []  # nothing new to store


def test_a_working_source_is_never_remembered(qapp: object) -> None:
    player = FakePlayer()
    sink = LearningSink()
    controller = PlayerController(lambda: player, incompatible_sink=sink)
    controller.setSourceLabel("Silo S01E01 DV · HDR10", "web")
    controller.play("http://s/dv8.mp4")
    player.dv_profile = 8

    player.trigger_tracks_changed()

    assert sink.seen == []


class RecordingCapability:
    """Stands in for PlaybackCapability: records what it was told and reports
    whether the verdict moved, exactly as the real one does."""

    def __init__(self, changes: bool = True) -> None:
        self.changes = changes
        self.seen: list[tuple[DecodeReport, float]] = []

    def observe(self, report: DecodeReport, played_s: float) -> bool:
        self.seen.append((report, played_s))
        return self.changes


def test_decode_evidence_is_filed_once_with_the_time_actually_played(qapp: object) -> None:
    player = FakePlayer()
    capability = RecordingCapability()
    controller = PlayerController(lambda: player, capability_sink=capability)
    controller.play("http://s/v.mkv")
    player.decode = DecodeReport(codec="AV1", height=2160, dropped_frames=90)
    for _ in range(3):
        controller._on_tick()
    assert capability.seen == []  # nothing is filed mid-playback

    controller.stop()
    ((report, played_s),) = capability.seen
    assert report.codec == "AV1"
    # Three ticks of the 5s autosave timer is fifteen seconds of playback.
    assert played_s == 15.0


def test_a_paused_playback_adds_no_time_to_the_evidence(qapp: object) -> None:
    player = FakePlayer()
    capability = RecordingCapability()
    controller = PlayerController(lambda: player, capability_sink=capability)
    controller.play("http://s/v.mkv")
    player.decode = DecodeReport(codec="hevc", height=2160, dropped_frames=5)
    controller._on_tick()
    controller.pause()
    for _ in range(10):
        controller._on_tick()  # the timer fires; the player is paused
    controller.stop()
    assert capability.seen[0][1] == 5.0


def test_a_new_file_starts_the_evidence_over(qapp: object) -> None:
    player = FakePlayer()
    capability = RecordingCapability()
    controller = PlayerController(lambda: player, capability_sink=capability)
    controller.play("http://s/one.mkv")
    player.decode = DecodeReport(codec="AV1", height=2160, dropped_frames=900)
    controller._on_tick()
    # A second play() without a stop() in between: mpv's counters reset with
    # the file, so the count from the first must not follow it.
    controller.play("http://s/two.mkv")
    player.decode = DecodeReport(codec="hevc", height=1080, dropped_frames=2)
    controller._on_tick()
    controller.stop()
    ((report, played_s),) = capability.seen
    assert (report.codec, report.dropped_frames) == ("hevc", 2)
    assert played_s == 5.0


def test_a_changed_verdict_and_the_speed_samples_share_one_settings_write(
    qapp: object,
) -> None:
    player = FakePlayer()
    writes: list[int] = []
    controller = PlayerController(
        lambda: player,
        speed_sink=RecordingSink(),
        speed_persist=lambda: writes.append(1),
        capability_sink=RecordingCapability(changes=True),
    )
    controller.play("http://s/v.mkv")
    player.speed = 3_750_000.0
    player.decode = DecodeReport(codec="AV1", height=2160, dropped_frames=900)
    controller._on_tick()
    controller.stop()
    assert writes == [1]


def test_a_verdict_that_did_not_move_writes_nothing(qapp: object) -> None:
    player = FakePlayer()
    writes: list[int] = []
    controller = PlayerController(
        lambda: player,
        speed_persist=lambda: writes.append(1),
        capability_sink=RecordingCapability(changes=False),
    )
    controller.play("/home/me/film.mkv")  # a local file samples no speed
    player.decode = DecodeReport(codec="hevc", height=1080, dropped_frames=0)
    controller._on_tick()
    controller.stop()
    assert writes == []


def test_nothing_is_asked_of_the_player_without_a_capability_sink(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    controller._on_tick()
    controller.stop()
    # The seam stays inert: a player fake with no decode_report() at all would
    # still work, which is what keeps the port addition from reaching tests
    # that have nothing to do with it.
    assert controller._decode_ticks == 0


class RecordingAccelerator:
    """Stands in for the segmented proxy: rewrites http URLs to a local one and
    reports a fixed upstream rate."""

    def __init__(
        self, rate: float = 0.0, failure: str | None = None, *, disk_cache: bool = False
    ) -> None:
        self.rate = rate
        self.failure = failure
        self.disk_cache = disk_cache
        self.registered: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def local_url(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str:
        if not url.startswith("http"):
            return url
        self.registered.append((url, tuple(headers)))
        return f"http://127.0.0.1:9/{len(self.registered)}"

    def upstream_bytes_per_s(self) -> float:
        return self.rate

    def holds_stream_cache(self) -> bool:
        return self.disk_cache

    def last_failure(self) -> str | None:
        return self.failure


def test_playback_goes_through_the_accelerator_with_the_streams_headers(
    qapp: object,
) -> None:
    player = FakePlayer()
    accelerator = RecordingAccelerator()
    controller = PlayerController(lambda: player, accelerator=accelerator)
    controller.play("http://s/v.mkv", {"Referer": "https://addon.test/"})
    assert player.calls == ["play:http://127.0.0.1:9/1"]
    assert accelerator.registered == [("http://s/v.mkv", (("Referer", "https://addon.test/"),))]
    # The proxy headers still reach mpv: it is talking to the proxy, but a
    # passthrough stream forwards whatever it is given.
    assert player.headers == (("Referer", "https://addon.test/"),)


def test_mpv_is_told_when_the_accelerator_holds_the_stream_on_disk(qapp: object) -> None:
    """One copy of the stream, not two: mpv keeps a small cache of its own only
    when it is actually reading the disk-backed proxy."""
    player = FakePlayer()
    controller = PlayerController(lambda: player, accelerator=RecordingAccelerator(disk_cache=True))
    controller.play("http://s/v.mkv")
    assert player.upstream_cached is True
    # A URL the accelerator hands straight back is mpv's alone to cache.
    controller.play("/home/me/film.mkv")
    assert player.upstream_cached is False

    memory_only = FakePlayer()
    controller = PlayerController(lambda: memory_only, accelerator=RecordingAccelerator())
    controller.play("http://s/v.mkv")
    assert memory_only.upstream_cached is False


def test_a_url_the_accelerator_declines_is_played_as_it_is(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, accelerator=RecordingAccelerator())
    controller.play("/home/me/film.mkv")
    assert player.calls == ["play:/home/me/film.mkv"]


def test_an_accelerator_that_raises_does_not_stop_playback(qapp: object) -> None:
    class Broken:
        def local_url(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str:
            raise RuntimeError("boom")

        def upstream_bytes_per_s(self) -> float:
            return 0.0

        def holds_stream_cache(self) -> bool:
            return True

        def last_failure(self) -> str | None:
            return None

    player = FakePlayer()
    controller = PlayerController(lambda: player, accelerator=Broken())
    controller.play("http://s/v.mkv")
    assert player.calls == ["play:http://s/v.mkv"]


def test_a_reconnect_registers_the_upstream_url_again(qapp: object) -> None:
    """The accelerator tore its fetch down when mpv dropped the connection, so
    the old local URL names a stream nothing is filling."""
    player = FakePlayer()
    accelerator = RecordingAccelerator()
    controller = PlayerController(lambda: player, accelerator=accelerator)
    controller.play("http://s/v.mkv")
    controller._stall_position = 120.0
    controller._reconnecting = True
    controller._reconnect_now()
    assert [url for url, _ in accelerator.registered] == ["http://s/v.mkv", "http://s/v.mkv"]
    assert player.calls[-1] == "play:http://127.0.0.1:9/2"


def test_the_speed_sample_comes_from_the_host_not_from_loopback(qapp: object) -> None:
    player = FakePlayer()
    player.speed = 500_000_000.0  # mpv draining a local socket
    sink = RecordingSink()
    controller = PlayerController(
        lambda: player,
        speed_sink=sink,
        accelerator=RecordingAccelerator(rate=2_500_000.0),
    )
    controller.play("http://s/v.mkv")
    controller._on_tick()
    assert sink.samples == [20_000]  # 2.5 MB/s, not mpv's loopback reading


def test_the_player_is_still_the_source_when_nothing_is_proxied(qapp: object) -> None:
    player = FakePlayer()
    player.speed = 3_750_000.0
    sink = RecordingSink()
    controller = PlayerController(
        lambda: player, speed_sink=sink, accelerator=RecordingAccelerator(rate=0.0)
    )
    # Handed straight back by the accelerator: mpv reads it itself.
    controller.play("ftp://s/v.mkv")
    controller._on_tick()
    assert sink.samples == [30_000]


def test_an_idle_proxy_is_no_sample_rather_than_mpvs_loopback_reading(qapp: object) -> None:
    """A proxy that has fetched nothing lately (read-ahead full, or mpv
    re-reading its disk cache) reports 0.0, and mpv's cache-speed is then the
    speed of a loopback socket off local disk. Recording that put 843 Mbps
    into the estimate on a 260 Mbps line."""
    player = FakePlayer()
    player.speed = 105_000_000.0  # mpv draining the proxy's disk cache
    sink = RecordingSink()
    controller = PlayerController(
        lambda: player, speed_sink=sink, accelerator=RecordingAccelerator(rate=0.0)
    )
    controller.play("http://s/v.mkv")
    controller._on_tick()
    assert sink.samples == []


def test_a_source_that_never_opens_gives_up_quickly_and_says_why(qapp: object) -> None:
    """A host that refuses the connection is not a network blip.

    Twenty retries over ten minutes is right for a stream that dropped: the
    network usually comes back. It is wrong for a source that never produced a
    frame -- the viewer is sitting in front of a list of other sources, and
    what they get instead is ten minutes of spinner.
    """
    player = FakePlayer()
    player._duration = 0.0
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.errorOccurred.connect(errors.append)
    controller.play("http://s/v.mkv")

    for _ in range(PlayerController.MAX_OPEN_ATTEMPTS + 1):
        controller._on_stream_ended()
        controller._reconnect_timer.stop()
        controller._reconnect_now()

    assert errors == [PlayerController.UNREACHABLE_ERROR]
    assert controller._reconnect_attempt <= PlayerController.MAX_OPEN_ATTEMPTS + 1
    assert not controller.reconnecting


def test_the_unreachable_message_names_the_host_that_refused(qapp: object) -> None:
    """ "It didn't respond" reads like a broken app.

    Every stream URL is an addon signing endpoint that redirects to a CDN
    node, so the only layer that knows which host actually refused is the
    accelerator. When it says, the viewer gets told.
    """
    player = FakePlayer()
    player._duration = 0.0
    errors: list[str] = []
    accelerator = RecordingAccelerator(failure="cdn-7.example refused the connection.")
    controller = PlayerController(lambda: player, accelerator=accelerator)
    controller.errorOccurred.connect(errors.append)
    controller.play("http://s/v.mkv")

    for _ in range(PlayerController.MAX_OPEN_ATTEMPTS + 1):
        controller._on_stream_ended()
        controller._reconnect_timer.stop()
        controller._reconnect_now()

    assert errors == ["cdn-7.example refused the connection. Try another source from the list."]


def test_an_accelerator_with_nothing_to_report_keeps_the_plain_message(qapp: object) -> None:
    player = FakePlayer()
    player._duration = 0.0
    errors: list[str] = []
    controller = PlayerController(lambda: player, accelerator=RecordingAccelerator())
    controller.errorOccurred.connect(errors.append)
    controller.play("http://s/v.mkv")

    for _ in range(PlayerController.MAX_OPEN_ATTEMPTS + 1):
        controller._on_stream_ended()
        controller._reconnect_timer.stop()
        controller._reconnect_now()

    assert errors == [PlayerController.UNREACHABLE_ERROR]


def test_a_stream_that_played_first_still_gets_the_patient_recovery(qapp: object) -> None:
    """The opposite case, and the reason the two are told apart at all: a
    dropped connection mid-episode deserves every one of its attempts."""
    player = FakePlayer()
    player._duration = 3600.0
    player._position = 900.0
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.errorOccurred.connect(errors.append)
    controller.play("http://s/v.mkv")

    for _ in range(PlayerController.MAX_OPEN_ATTEMPTS + 2):
        controller._on_stream_ended()
        controller._reconnect_timer.stop()
        controller._reconnect_now()

    assert errors == []
    assert controller.reconnecting


def test_a_source_that_never_opens_moves_to_the_next_one(qapp: object) -> None:
    """A debrid CDN node that refuses the connection takes every source
    pointing at it with it, and says nothing about the title. Showing an error
    asks the viewer to click the next row themselves; the app can do that."""
    player = FakePlayer()
    player._duration = 0.0
    warnings: list[str] = []
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.playbackWarning.connect(warnings.append)
    controller.errorOccurred.connect(errors.append)
    controller.setSourceQueue(
        [
            {"url": "http://s/second.mkv", "name": "1080p b", "headers": {"R": "x"}},
            {"url": "http://s/third.mkv", "name": "720p c", "headers": {}},
        ]
    )
    controller.play("http://s/first.mkv")

    controller._on_stream_ended()
    assert player.calls[-1] == "play:http://s/second.mkv"
    assert player.headers == (("R", "x"),)
    assert warnings == [PlayerController.SWITCHING_SOURCE]
    assert errors == []

    controller._on_stream_ended()
    assert player.calls[-1] == "play:http://s/third.mkv"


def test_the_error_is_only_for_when_there_is_nothing_left_to_try(qapp: object) -> None:
    player = FakePlayer()
    player._duration = 0.0
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.errorOccurred.connect(errors.append)
    controller.setSourceQueue([{"url": "http://s/second.mkv", "name": "b"}])
    controller.play("http://s/first.mkv")

    controller._on_stream_ended()  # switches to the second
    for _ in range(PlayerController.MAX_OPEN_ATTEMPTS + 1):
        controller._on_stream_ended()
        controller._reconnect_timer.stop()
        controller._reconnect_now()

    assert errors == [PlayerController.UNREACHABLE_ERROR]


def test_an_error_clip_in_place_of_the_title_moves_to_the_next_source(qapp: object) -> None:
    # ElfHosted answers a link signed for another address with a "Wrong IP"
    # video: it opens, plays a second with no duration, and ends. That is a
    # source that failed, not a connection that dropped.
    player = FakePlayer()
    player._duration = 0.0
    warnings: list[str] = []
    controller = PlayerController(lambda: player)
    controller.playbackWarning.connect(warnings.append)
    controller.setMediaContext({"mediaId": "tt1", "videoId": "tt1:1:1", "type": "series"})
    controller.setSourceQueue([{"url": "http://s/second.mkv", "name": "b"}])
    controller.play("http://s/first.mkv")
    controller._note_source_opened()
    player._position = 1.0

    controller._on_stream_ended()
    assert player.calls[-1] == "play:http://s/second.mkv"
    assert warnings == [PlayerController.SWITCHING_SOURCE]
    assert not controller.reconnecting


def test_an_error_clip_with_nothing_left_says_so_instead_of_reconnecting(
    qapp: object,
) -> None:
    player = FakePlayer()
    player._duration = 8.0
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.errorOccurred.connect(errors.append)
    controller.setMediaContext({"mediaId": "tt1", "videoId": "tt1", "type": "movie"})
    controller.play("http://s/only.mkv")
    controller._note_source_opened()
    player._position = 8.0

    controller._on_stream_ended()
    assert errors == [PlayerController.SLATE_ERROR]
    assert not controller.reconnecting
    assert not controller._reconnect_timer.isActive()


def test_an_error_clip_is_never_recorded_as_progress(qapp: object) -> None:
    # Its position would overwrite where the viewer left the episode.
    player = FakePlayer()
    player._duration = 10.0
    player._position = 5.0
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext({"mediaId": "tt1", "videoId": "tt1", "type": "movie"})
    controller.play("http://s/only.mkv")
    controller._record()
    assert progress.records == []


def test_a_short_trailer_is_not_an_error_clip(qapp: object) -> None:
    # Trailers carry no media identity, and may be short.
    player = FakePlayer()
    player._duration = 30.0
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.errorOccurred.connect(errors.append)
    controller.setMediaContext({})
    controller.play("http://s/trailer.mp4")
    controller._note_source_opened()
    player._position = 30.0
    controller._on_stream_ended()
    assert errors == []


def test_badges_name_the_file_and_clear_for_the_next_one(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    changed: list[int] = []
    controller.mediaFormatChanged.connect(lambda: changed.append(1))
    controller.play("http://s/film.mkv")
    player._format = MediaFormat(
        hdr="Dolby Vision",
        dolby_vision_profile=5,
        resolution="4K",
        immersive_audio="Dolby Atmos",
        channels="7.1",
    )
    controller._refresh_media_format()
    labels = [b["label"] for b in controller.mediaBadges]
    assert labels == ["Dolby Vision", "4K", "Dolby Atmos", "7.1"]
    # Which mark each shows (the logos under qml/formats/, or a glyph).
    formats = [b["format"] for b in controller.mediaBadges]
    assert formats == ["dolby-vision", "4k", "dolby-atmos", "channels"]
    assert "profile 5" in controller.mediaBadges[0]["detail"]
    assert "tone-mapped" in controller.mediaBadges[0]["detail"]
    # Asking again with nothing new changes nothing.
    count = len(changed)
    controller._refresh_media_format()
    assert len(changed) == count
    # The next file starts bare, not under the last one's badges.
    controller.play("http://s/next.mkv")
    assert controller.mediaBadges == []


def test_a_queue_is_capped_and_skips_entries_with_nothing_playable(qapp: object) -> None:
    controller = PlayerController(lambda: FakePlayer())
    controller.setSourceQueue([{"url": ""}, *({"url": f"http://s/{i}.mkv"} for i in range(10))])
    assert len(controller._queue) == PlayerController.SOURCE_QUEUE_LIMIT
    assert controller._queue[0][0] == "http://s/0.mkv"


def test_stopping_drops_the_queue(qapp: object) -> None:
    """Leaving the player is not a request to keep trying sources."""
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.setSourceQueue([{"url": "http://s/second.mkv"}])
    controller.play("http://s/first.mkv")
    controller.stop()
    assert controller._queue == []


def test_track_list_arrival_prepares_fast_switching(qapp: object) -> None:
    # The player can only make audio switching instant once it knows what the
    # tracks are, and it must be told from the GUI thread -- mpv's own track
    # callback runs on the thread it delivers events on.
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/film.mkv")
    assert player.prepared == 0

    player.trigger_tracks_changed()

    assert player.prepared == 1


def test_a_source_that_never_opens_is_noticed_without_an_end_of_file(qapp: object) -> None:
    """The failure that has no end of file to wait for.

    A host that refuses the connection gives mpv nothing to finish: it reports
    an END_FILE error and goes idle, `eof-reached` stays false, and every
    observer stays quiet. Before this signal existed that was an endless
    spinner -- no next source, no error, no message -- which is exactly what a
    dead debrid CDN node produced.
    """
    player = FakePlayer()
    player._duration = 0.0
    warnings: list[str] = []
    controller = PlayerController(lambda: player)
    controller.playbackWarning.connect(warnings.append)
    controller.setSourceQueue([{"url": "http://s/second.mkv", "name": "b"}])
    controller.play("http://s/first.mkv")

    player.trigger_load_failed()

    assert player.calls[-1] == "play:http://s/second.mkv"
    assert warnings == [PlayerController.SWITCHING_SOURCE]


def test_a_dead_source_with_nothing_to_fall_back_to_ends_in_an_error(qapp: object) -> None:
    """And when the list runs out, the viewer is told rather than left with
    the spinner they started with."""
    player = FakePlayer()
    player._duration = 0.0
    errors: list[str] = []
    controller = PlayerController(lambda: player)
    controller.errorOccurred.connect(errors.append)
    controller.play("http://s/only.mkv")

    for _ in range(PlayerController.MAX_OPEN_ATTEMPTS + 1):
        player.trigger_load_failed()
        controller._reconnect_timer.stop()
        controller._reconnect_now()

    assert errors == [PlayerController.UNREACHABLE_ERROR]


def test_the_next_episode_is_asked_for_once_as_this_one_ends(qapp: object) -> None:
    told: list[tuple[str, str]] = []
    player = FakePlayer()
    player._duration = 2400.0
    controller = PlayerController(
        lambda: player, on_nearing_end=lambda media, video: told.append((media, video))
    )
    controller.setMediaContext({"mediaId": "tt1", "videoId": "tt1:1:1", "type": "series"})
    controller.play("http://s/v.mkv")

    player._position = 2400.0 - PlayerController.NEARING_END_S - 30
    controller._on_tick()
    assert told == [], "still well before the end"
    player._position = 2400.0 - PlayerController.NEARING_END_S + 10
    controller._on_tick()
    controller._on_tick()
    assert told == [("tt1", "tt1:1:1")], "once per playback, not once per tick"

    controller.play("http://s/v.mkv")  # the next playback arms it again
    player._position = 2400.0 - 10
    controller._on_tick()
    assert len(told) == 2


def test_a_film_ending_asks_for_nothing(qapp: object) -> None:
    told: list[tuple[str, str]] = []
    player = FakePlayer()
    controller = PlayerController(
        lambda: player, on_nearing_end=lambda media, video: told.append((media, video))
    )
    controller.setMediaContext({"mediaId": "tt1", "videoId": "", "type": "movie"})
    controller.play("http://s/v.mkv")
    player._position = player._duration - 1
    controller._on_tick()
    assert told == []


def test_a_source_whose_host_never_answers_moves_on_after_the_open_timeout(qapp: object) -> None:
    # A host that accepts the connection and then says nothing produces no
    # load error and no end of file; only the watchdog notices.
    player = FakePlayer()
    player._duration = 0.0
    controller = PlayerController(lambda: player)
    controller.setSourceQueue([{"url": "http://s/second.mkv", "name": "b"}])
    controller.play("http://s/first.mkv")
    assert controller._open_watchdog.isActive()

    controller._open_watchdog.timeout.emit()
    assert player.calls[-1] == "play:http://s/second.mkv"
    assert controller._open_watchdog.isActive(), "the next source gets its own deadline"


def test_a_source_that_opens_disarms_the_watchdog(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/first.mkv")
    player.trigger_opened()
    assert not controller._open_watchdog.isActive()


def test_a_reported_failure_is_not_handled_twice(qapp: object) -> None:
    # The load error arrived first; the watchdog must not act on the same
    # source again a few seconds later.
    player = FakePlayer()
    player._duration = 0.0
    controller = PlayerController(lambda: player)
    controller.play("http://s/first.mkv")
    player.trigger_load_failed()
    assert not controller._open_watchdog.isActive()


def test_stop_disarms_the_watchdog(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/first.mkv")
    controller.stop()
    assert not controller._open_watchdog.isActive()


class _Links:
    """Stands in for LinkWarmup: one resolved link, handed out once."""

    def __init__(self, original: str, resolved: str) -> None:
        self._links = {original: resolved}

    def take(self, url: str) -> str | None:
        return self._links.pop(url, None)


def test_play_starts_on_the_link_resolved_ahead_of_the_click(qapp: object) -> None:
    player = FakePlayer()
    player._duration = 0.0
    links = _Links("http://addon/play", "http://cdn/file.mkv")
    controller = PlayerController(lambda: player, links=links)  # type: ignore[arg-type]
    controller.setSourceQueue([{"url": "http://addon/second", "name": "b"}])
    controller.play("http://addon/play")
    assert player.calls[-1] == "play:http://cdn/file.mkv"

    # The resolved link failed to open (expired, say): the addon's own URL is
    # tried next, through its redirect, before any other source.
    controller._on_stream_ended()
    assert player.calls[-1] == "play:http://addon/play"
    controller._on_stream_ended()
    assert player.calls[-1] == "play:http://addon/second"


def test_a_reconnect_asks_the_addon_for_a_fresh_link(qapp: object) -> None:
    # An hour in, the resolved CDN link may have expired; a dropped
    # connection reloads the addon's URL, not the link it started on.
    player = FakePlayer()
    links = _Links("http://addon/play", "http://cdn/file.mkv")
    controller = PlayerController(lambda: player, links=links)  # type: ignore[arg-type]
    controller.play("http://addon/play")
    assert controller._current_url == "http://addon/play"


def test_each_new_file_opens_with_the_preferred_languages(qapp: object) -> None:
    """Read at every play(), so a change in Settings reaches the next episode
    with no signal in between."""
    from gravitas.domain.models import TrackLanguages

    player = FakePlayer()
    preference = [TrackLanguages(audio="ja", subtitle="en")]
    controller = PlayerController(lambda: player, languages_provider=lambda: preference[0])
    controller.play("http://s/v.mkv")
    assert player.calls[-2:] == [f"languages:{preference[0]}", "play:http://s/v.mkv"]

    preference[0] = TrackLanguages(audio="de")
    controller.play("http://s/next.mkv")
    assert player.calls[-2:] == [f"languages:{preference[0]}", "play:http://s/next.mkv"]


def test_a_reconnect_keeps_the_track_the_viewer_chose(qapp: object) -> None:
    """A reload of the same file must not put the preference back over a
    track switched to mid-episode."""
    from gravitas.domain.models import TrackLanguages

    player = FakePlayer()
    controller = PlayerController(
        lambda: player, languages_provider=lambda: TrackLanguages(audio="ja")
    )
    controller.play("http://s/v.mkv")
    player.calls.clear()
    controller._stall_position = 120.0
    controller._reconnecting = True
    controller._reconnect_now()
    assert player.calls == ["play:http://s/v.mkv"]


class RecordingInhibitor:
    def __init__(self) -> None:
        self.wishes: list[bool] = []

    def set_inhibited(self, inhibited: bool) -> None:
        self.wishes.append(inhibited)


def test_the_screen_stays_awake_only_while_a_video_plays(qapp: object) -> None:
    player = FakePlayer()
    inhibitor = RecordingInhibitor()
    controller = PlayerController(lambda: player, idle_inhibitor=inhibitor)
    controller.play("http://s/v.mkv")
    assert inhibitor.wishes == [True]
    # pause()/resume() leave the notification to mpv's `pause` observer, as
    # the real player does; the fake is told to fire it.
    controller.pause()
    assert player._state_changed_callback is not None
    player._state_changed_callback()
    controller.resume()
    player._state_changed_callback()
    assert inhibitor.wishes == [True, False, True]
    controller.stop()
    assert inhibitor.wishes == [True, False, True, False]
    # A second stop, or any other state change with nothing playing, asks
    # nothing new of the desktop.
    controller.stop()
    assert inhibitor.wishes == [True, False, True, False]


def test_video_id_names_the_episode_playing(qapp: object) -> None:
    controller = PlayerController(lambda: FakePlayer())
    assert controller.videoId == ""
    controller.setMediaContext({"mediaId": "tt1", "videoId": "tt1:1:6", "type": "series"})
    assert controller.videoId == "tt1:1:6"


def test_a_resumed_source_that_never_opens_still_moves_to_the_next_one(qapp: object) -> None:
    """The resume point is not progress. `start` put 218s in the position
    before a byte was read, and "never opened" (no duration, no position) read
    that as a stream that had dropped at 218s: it was retried as a dropped
    connection instead of handing over to the next source."""
    player = FakePlayer()
    player._duration = 0.0
    controller = PlayerController(lambda: player, None, FakeProgress(resume=218.0))
    controller.setMediaContext(CONTEXT)
    controller.setSourceQueue([{"url": "http://s/second.mkv", "name": "b", "headers": {}}])
    controller.play("http://s/first.mkv")
    assert player.start == 218.0

    controller._on_stream_ended()  # never reported opening
    assert player.calls[-1] == "play:http://s/second.mkv"
    assert controller.reconnecting is False


def test_a_reload_that_lands_far_from_the_stall_is_not_a_recovery(qapp: object) -> None:
    """A reload whose seeks all failed could not read the file's index and came
    up at its last second. That was taken as the stream recovering (the
    playhead had moved past the stall), and the autosave recorded the episode
    as watched. It is neither: nothing is recorded, and the end of file it runs
    into is another failed attempt, not the episode finishing."""
    progress = FakeProgress()
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.trigger_opened()
    controller.RECONNECT_DELAYS_MS = (0, 0, 0)  # type: ignore[misc]
    player._position = 218.0
    player._duration = 2559.0
    controller.position()
    player.trigger_stream_ended()  # dropped at 218s
    assert controller.reconnecting is True
    progress.records.clear()

    # The reload happens, and comes up playing at the very end instead.
    from PySide6.QtTest import QTest

    QTest.qWait(20)
    assert player.calls[-1] == "play:http://s/v.mkv"
    player._paused = False  # mpv plays what it loaded
    player._position = 2559.0
    controller._on_tick()
    assert controller.reconnecting is True
    assert progress.records == []

    # Its end of file is not the episode finishing: another attempt is armed
    # at the stall point.
    player.start = 0.0
    calls = len(player.calls)
    player.trigger_stream_ended()
    QTest.qWait(20)
    assert player.calls[calls:] == ["play:http://s/v.mkv"]
    assert player.start == 218.0

    # Leaving now records where it stalled, not where mpv is sitting.
    controller._record()
    assert progress.records[-1]["position"] == 218.0


def test_the_files_chapters_mark_intro_and_credits(qapp: object) -> None:
    """Read once the track list (and with it the chapter list) is in: the
    skip button and the next-episode card go by these."""
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    player._duration = 2559.224
    player.chapter_list = [
        (0.0, "Chapter 01"),
        (6.01, "Intro"),
        (26.86, "Chapter 02"),
        (2492.11, "Credits"),
    ]
    assert controller.introStart == -1.0  # nothing read yet
    changes: list[None] = []
    controller.segmentsChanged.connect(lambda: changes.append(None))
    controller._tracksReady.emit()
    assert (controller.introStart, controller.introEnd) == (6.01, 26.86)
    assert controller.creditsStart == 2492.11
    assert controller.recapStart == -1.0
    assert len(changes) == 1
    # A new file starts with nothing known about it.
    controller.play("http://s/next.mkv")
    assert controller.introStart == -1.0 and controller.creditsStart == -1.0


def test_segments_wait_for_a_duration(qapp: object) -> None:
    """Which half of the file a chapter is in needs the length; the autosave
    tick reads them once it is known."""
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    player._duration = 0.0
    player.chapter_list = [(0.0, "A"), (2492.0, "Credits")]
    controller._tracksReady.emit()
    assert controller.creditsStart == -1.0
    player._duration = 2559.0
    controller._on_tick()
    assert controller.creditsStart == 2492.0


class RecordingSegmentSource:
    """Stands in for SkipDB: records asks, answers what it is told, and can
    hold its answer until released."""

    def __init__(self, answer: object = None) -> None:
        import asyncio

        self.asks: list[tuple[str, int, int, float]] = []
        self.answer = answer
        self.release = asyncio.Event()
        self.release.set()

    async def segments(self, imdb_id: str, season: int, episode: int, duration: float) -> object:
        self.asks.append((imdb_id, season, episode, duration))
        await self.release.wait()
        return self.answer


EPISODE = {"mediaId": "tt14824792", "videoId": "tt14824792:1:7", "type": "series"}


async def _settle() -> None:
    """Let background work finish, including what runs on a worker thread
    (asyncio.to_thread), which a bare sleep(0) does not wait for."""
    import asyncio

    for _ in range(20):
        await asyncio.sleep(0.005)


async def test_segments_fall_back_to_the_source_when_the_file_names_none(qapp: object) -> None:
    from gravitas.domain.models import Segments

    source = RecordingSegmentSource(Segments(intro=(229.5, 246.5), credits_start=3434.0))
    player = FakePlayer()
    controller = PlayerController(lambda: player, segment_source=source)
    controller.setMediaContext(EPISODE)
    controller.play("http://s/v.mkv")
    player._duration = 3500.0
    player.chapter_list = [(0.0, "Chapter 1"), (900.0, "Chapter 2")]  # says nothing
    controller._tracksReady.emit()
    await _settle()
    assert source.asks == [("tt14824792", 1, 7, 3500.0)]
    assert (controller.introStart, controller.introEnd) == (229.5, 246.5)
    assert controller.creditsStart == 3434.0


async def test_the_files_own_chapters_win_and_are_only_filled_in(qapp: object) -> None:
    from gravitas.domain.models import Segments

    source = RecordingSegmentSource(Segments(intro=(200.0, 230.0), credits_start=2400.0))
    player = FakePlayer()
    controller = PlayerController(lambda: player, segment_source=source)
    controller.setMediaContext(EPISODE)
    controller.play("http://s/v.mkv")
    player._duration = 2559.0
    player.chapter_list = [(0.0, "Chapter 01"), (6.01, "Intro"), (26.86, "Chapter 02")]
    controller._tracksReady.emit()
    await _settle()
    # The file's intro stays; only the credits it did not name come from the source.
    assert (controller.introStart, controller.introEnd) == (6.01, 26.86)
    assert controller.creditsStart == 2400.0


async def test_nothing_is_asked_when_the_file_names_everything(qapp: object) -> None:
    source = RecordingSegmentSource()
    player = FakePlayer()
    controller = PlayerController(lambda: player, segment_source=source)
    controller.setMediaContext(EPISODE)
    controller.play("http://s/v.mkv")
    player._duration = 2559.224
    player.chapter_list = [(0.0, "A"), (6.01, "Intro"), (26.86, "B"), (2492.11, "Credits")]
    controller._tracksReady.emit()
    await _settle()
    assert source.asks == []


async def test_nothing_is_asked_with_the_setting_off_or_for_a_movie(qapp: object) -> None:
    source = RecordingSegmentSource()
    player = FakePlayer()
    enabled = [False]
    controller = PlayerController(
        lambda: player, segment_source=source, segment_lookup_enabled=lambda: enabled[0]
    )
    controller.setMediaContext(EPISODE)
    controller.play("http://s/v.mkv")
    player._duration = 2559.0
    controller._tracksReady.emit()
    await _settle()
    assert source.asks == []
    # On, but a movie: SkipDB is asked by episode.
    enabled[0] = True
    controller.setMediaContext({"mediaId": "tt0111161", "videoId": "", "type": "movie"})
    controller.play("http://s/film.mkv")
    controller._tracksReady.emit()
    await _settle()
    assert source.asks == []


async def test_a_late_answer_for_the_last_episode_is_dropped(qapp: object) -> None:
    from gravitas.domain.models import Segments

    source = RecordingSegmentSource(Segments(credits_start=2400.0))
    source.release.clear()
    player = FakePlayer()
    controller = PlayerController(lambda: player, segment_source=source)
    controller.setMediaContext(EPISODE)
    controller.play("http://s/e7.mkv")
    player._duration = 2559.0
    controller._tracksReady.emit()
    await _settle()
    # The next episode starts before SkipDB answers for this one.
    controller.play("http://s/e8.mkv")
    source.release.set()
    await _settle()
    assert controller.creditsStart == -1.0


def test_the_timeline_is_split_at_the_files_sections(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    assert controller.timelineSections == []
    player._duration = 2559.224
    player.chapter_list = [
        (0.0, "Chapter 01"),
        (6.01, "Intro"),
        (26.86, "Chapter 02"),
        (2492.11, "Credits"),
    ]
    controller._tracksReady.emit()
    assert [(s["start"], s["title"], s["kind"]) for s in controller.timelineSections] == [
        (0.0, "Chapter 1", ""),
        (6.01, "Intro", "intro"),
        (26.86, "Chapter 2", ""),
        (2492.11, "Credits", "credits"),
    ]
    controller.play("http://s/next.mkv")
    assert controller.timelineSections == []


def _subtitle_controller(
    offered: list[object], preferred: str = "en"
) -> tuple[PlayerController, FakePlayer, list[tuple[str, str]]]:
    from gravitas.domain.models import TrackLanguages

    asked: list[tuple[str, str]] = []

    async def finder(kind: str, video: str) -> list[object]:
        asked.append((kind, video))
        return offered

    player = FakePlayer()
    controller = PlayerController(
        lambda: player,
        languages_provider=lambda: TrackLanguages(subtitle=preferred),
        subtitle_finder=finder,  # type: ignore[arg-type]
    )
    controller.setMediaContext(EPISODE)
    return controller, player, asked


def _subs() -> list[object]:
    from gravitas.domain.models import Subtitle

    return [
        Subtitle(url="https://s/en1.srt", lang="eng", label="Release A", addon="AIOStreams"),
        Subtitle(url="https://s/en2.srt", lang="eng", label="Release B", addon="AIOStreams"),
        Subtitle(url="https://s/es.srt", lang="spa", label="ES", addon="AIOStreams"),
        Subtitle(url="https://s/pob.srt", lang="pob", label="BR", addon="AIOStreams"),
    ]


async def test_a_file_without_the_preferred_language_gets_an_addons(qapp: object) -> None:
    """No track in the preferred language, by tag or by title: the best file
    an addon offers in it is shown, once."""
    controller, player, asked = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.sub = None  # mpv found nothing, and neither did the fallback
    controller._tracksReady.emit()
    await _settle()
    assert asked == [("series", "tt14824792:1:7")]
    assert player.added == [("https://s/en1.srt", "English · Release A", "eng", True)]
    # Another track-list change (the added file itself) does not add another.
    controller._tracksReady.emit()
    await _settle()
    assert len(player.added) == 1


async def test_a_file_with_the_preferred_language_is_left_alone(qapp: object) -> None:
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.sub = 2  # the file's own English track
    controller._tracksReady.emit()
    await _settle()
    assert getattr(player, "added", []) == []


async def test_the_menu_lists_addon_files_after_the_files_own(qapp: object) -> None:
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.sub = 2
    controller._tracksReady.emit()
    await _settle()
    titles = [entry["title"] for entry in controller.subtitleTracks()]
    assert titles == [
        "English",  # the file's own track
        "English · Release A (Online)",
        "English · Release B (Online)",
        "Portuguese (Brazil) · BR (Online)",
        "Spanish · ES (Online)",
    ]
    # Picking one loads it; it then leaves the "not yet loaded" part of the menu.
    pick = next(e for e in controller.subtitleTracks() if e["title"].startswith("Spanish"))
    controller.selectSubtitle(pick["id"])  # type: ignore[arg-type]
    await _settle()
    assert player.added == [("https://s/es.srt", "Spanish · ES", "spa", True)]
    assert all("Spanish" not in e["title"] for e in controller.subtitleTracks())


def _slow_subtitles(player: FakePlayer) -> threading.Event:
    """Makes the player's add_subtitle block, as a download does, until the
    returned event is set."""
    arrived = threading.Event()
    add = player.add_subtitle

    def slow(url: str, title: str, lang: str, *, select: bool = True) -> None:
        arrived.wait(5)
        add(url, title, lang, select=select)

    player.add_subtitle = slow  # type: ignore[method-assign]
    return arrived


async def test_an_addon_file_shows_as_chosen_while_it_downloads(qapp: object) -> None:
    """The menu marks the pick at the click, not seconds later when the
    player has the file."""
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.sub = 2
    controller._tracksReady.emit()
    await _settle()
    arrived = _slow_subtitles(player)
    changes: list[bool] = []
    controller.subtitleTracksChanged.connect(lambda: changes.append(True))
    pick = next(e for e in controller.subtitleTracks() if e["title"].startswith("Spanish"))
    controller.selectSubtitle(pick["id"])  # type: ignore[arg-type]
    assert changes, "an open menu hears of the pick at once"
    assert controller.currentSubtitle() == pick["id"]
    loading = next(e for e in controller.subtitleTracks() if e["id"] == pick["id"])
    assert loading["title"] == "Spanish · ES (loading\u2026)"
    arrived.set()
    await _settle()
    # Loaded: the player's own track, and no longer offered as a download.
    assert controller.currentSubtitle() == 901
    assert all("Spanish" not in e["title"] for e in controller.subtitleTracks())


async def test_a_track_chosen_while_a_file_downloads_stays_on_screen(qapp: object) -> None:
    """The player shows a file as it adds it; one the viewer has since
    turned away from must not take the screen back."""
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.sub = 2
    controller._tracksReady.emit()
    await _settle()
    arrived = _slow_subtitles(player)
    pick = next(e for e in controller.subtitleTracks() if e["title"].startswith("Spanish"))
    controller.selectSubtitle(pick["id"])  # type: ignore[arg-type]
    controller.selectSubtitle(2)  # changed their mind while it downloads
    assert controller.currentSubtitle() == 2
    arrived.set()
    await _settle()
    assert player.added == [("https://s/es.srt", "Spanish · ES", "spa", True)]
    assert controller.currentSubtitle() == 2


async def test_a_file_that_fails_to_download_is_no_longer_shown_as_chosen(
    qapp: object,
) -> None:
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.sub = 2
    controller._tracksReady.emit()
    await _settle()

    def refuse(url: str, title: str, lang: str, *, select: bool = True) -> None:
        raise OSError("the host went away")

    player.add_subtitle = refuse  # type: ignore[method-assign]
    pick = next(e for e in controller.subtitleTracks() if e["title"].startswith("Spanish"))
    controller.selectSubtitle(pick["id"])  # type: ignore[arg-type]
    await _settle()
    assert controller.currentSubtitle() == 2
    offered = next(e for e in controller.subtitleTracks() if e["id"] == pick["id"])
    assert offered["title"] == "Spanish · ES (Online)"


async def test_a_reconnect_puts_the_addon_subtitles_back(qapp: object) -> None:
    """Reloading the file drops every file added to the old load."""
    from PySide6.QtTest import QTest

    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player.trigger_opened()
    player.sub = None
    controller._tracksReady.emit()
    await _settle()
    assert len(player.added) == 1
    controller.RECONNECT_DELAYS_MS = (0, 0, 0)  # type: ignore[misc]
    player._position = 600.0
    player._duration = 2600.0
    controller.position()
    player.trigger_stream_ended()
    QTest.qWait(20)  # the reload happens
    controller._tracksReady.emit()
    await _settle()
    assert player.added[-1] == ("https://s/en1.srt", "English · Release A", "eng", True)
    assert len(player.added) == 2


async def test_the_empty_track_list_of_a_loading_file_settles_nothing(qapp: object) -> None:
    """mpv reports an empty track list before the real one (on registration,
    and as each new file starts loading). Judged then, a file with its own
    English track looked subtitle-less and got an out-of-sync addon file."""
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player._tracks, player._audio = [], []  # still loading
    player.sub = None
    controller._tracksReady.emit()
    await _settle()
    # The real list arrives, with the file's own English track selected.
    player._tracks, player._audio = [(2, "English")], [(1, "eng · 5.1")]
    player.sub = 2
    controller._tracksReady.emit()
    await _settle()
    assert getattr(player, "added", []) == []


async def test_the_files_own_track_wins_before_mpv_has_chosen_it(qapp: object) -> None:
    """mpv lists a file's tracks before it selects among them. Judged by the
    selection then, a file with its own English track looked subtitle-less
    and got an addon's file timed to another release."""
    controller, player, _ = _subtitle_controller(_subs())
    controller.play("http://s/v.mkv")
    player._tracks = [(2, "English"), (3, "English · SDH")]
    player.sub = None  # listed, not yet chosen
    player.file_has_preferred = True
    controller._tracksReady.emit()
    await _settle()
    assert getattr(player, "added", []) == []


async def test_the_file_made_for_the_playing_release_comes_first(qapp: object) -> None:
    """An addon's file is timed to its uploader's release; the one naming the
    same group and resolution as the source playing usually lines up."""
    from gravitas.domain.models import Subtitle

    offered = [
        Subtitle(url="https://s/a.srt", lang="eng", label="South Park - S06E01"),
        Subtitle(url="https://s/b.srt", lang="eng", label="South.Park.S06E01.DVDRip.XviD-SAiNTS"),
        Subtitle(
            url="https://s/c.srt",
            lang="eng",
            label="South Park S06E01 Jared Has Aides (1920x1080) [Phr0stY]",
        ),
    ]
    controller, player, _ = _subtitle_controller(offered)
    controller.setSourceLabel("1080p", "South Park S06E01 1080p BluRay x264 Phr0stY")
    controller.play("http://s/v.mkv")
    player.sub = None
    controller._tracksReady.emit()
    await _settle()
    assert player.added[0][0] == "https://s/c.srt"


def test_subtitles_shift_in_tenths_and_report_the_offset(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    controller.play("http://s/v.mkv")
    assert controller.shiftSubtitles(0.1) == 0.1
    assert controller.shiftSubtitles(0.1) == 0.2
    assert controller.shiftSubtitles(-0.3) == -0.1
    assert player.subtitle_delay() == -0.1


async def test_the_file_name_says_which_episode_the_subtitles_belong_to(qapp: object) -> None:
    """One addon's "S06E01" was two episodes depending on the release, and its
    label named neither: the file name does, and the subtitles made for that
    episode come first. The labels are the seven AIOStreams offered (real)."""
    from gravitas.domain.models import Subtitle

    labels = [
        "South Park - S06E01",
        "South Park S06E01 Jared Has Aides (1920x1080) [Phr0stY]",
        "South Park - s06e01 - Freak Strike",
        "South Park S06E01 Jared Has Aides.DVD.NonHI.pcc.en.COMCAST",
        "South Park S06E01 Jared Has Aides.DVD.HI.pcc.en.COMCAST",
        "6x01- Jared Has Aides",
        "S6E01 - Freak Strike",
    ]
    offered = [
        Subtitle(url=f"https://s/{i}.srt", lang="eng", label=t) for i, t in enumerate(labels)
    ]
    controller, _player, _ = _subtitle_controller(offered)
    controller.setSourceLabel("AIOStreams", "☁︎ South Park s06·e01")
    controller.setSourceFile("S06E01 - Freak Strike.mkv")
    controller.play("http://s/v.mkv")
    await _settle()
    ranked = [s.label for s in controller._online_in("en")]
    assert ranked[:2] == ["South Park - s06e01 - Freak Strike", "S6E01 - Freak Strike"]
