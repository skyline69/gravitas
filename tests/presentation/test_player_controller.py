from collections.abc import Callable, Sequence

from gravitas.presentation.controllers.player_controller import PlayerController


class FakePlayer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.sub: int | None = -1
        self.aud: int | None = -1
        self._paused = False
        self._muted = False
        self.loading = False
        self.buffered = 0.0
        self._volume = 100.0
        self._position = 0.0
        self.start = 0.0
        self.headers: tuple[tuple[str, str], ...] = ()
        self._duration = 100.0
        self._tracks: list[tuple[int, str]] = [(2, "English")]
        self._audio: list[tuple[int, str]] = [(1, "eng · 5.1")]
        self._tracks_changed_callback: Callable[[], None] | None = None
        self._state_changed_callback: Callable[[], None] | None = None

    def play(
        self, url: str, *, start: float = 0.0, headers: Sequence[tuple[str, str]] = ()
    ) -> None:
        if self.fail:
            from gravitas.domain.errors import PlaybackFailed

            raise PlaybackFailed("boom")
        self.start = start
        self.headers = tuple(headers)
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

    def render_handle(self) -> object | None:
        return None

    def apply_subtitle_style(self, style: object) -> None:
        self.calls.append(f"style:{style}")

    def trigger_tracks_changed(self, tracks: list[tuple[int, str]] | None = None) -> None:
        if tracks is not None:
            self._tracks = tracks
        if self._tracks_changed_callback is not None:
            self._tracks_changed_callback()

    def trigger_state_changed(self) -> None:
        if self._state_changed_callback is not None:
            self._state_changed_callback()

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
