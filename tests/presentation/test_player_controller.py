from collections.abc import Callable

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
        self._volume = 100.0
        self._position = 0.0
        self._duration = 100.0
        self._tracks: list[tuple[int, str]] = [(2, "English")]
        self._audio: list[tuple[int, str]] = [(1, "eng · 5.1")]
        self._tracks_changed_callback: Callable[[], None] | None = None
        self._state_changed_callback: Callable[[], None] | None = None

    def play(self, url: str) -> None:
        if self.fail:
            from gravitas.domain.errors import PlaybackFailed

            raise PlaybackFailed("boom")
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
