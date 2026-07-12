from collections.abc import Callable

from gravitas.presentation.controllers.player_controller import PlayerController


class FakePlayer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.sub: int | None = -1
        self._tracks: list[tuple[int, str]] = [(2, "English")]
        self._tracks_changed_callback: Callable[[], None] | None = None

    def play(self, url: str) -> None:
        if self.fail:
            from gravitas.domain.errors import PlaybackFailed

            raise PlaybackFailed("boom")
        self.calls.append(f"play:{url}")

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")

    def seek(self, seconds: float) -> None:
        self.calls.append(f"seek:{seconds}")

    def set_subtitle_track(self, track_id: int | None) -> None:
        self.sub = track_id

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return self._tracks

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._tracks_changed_callback = callback

    def trigger_tracks_changed(self, tracks: list[tuple[int, str]] | None = None) -> None:
        if tracks is not None:
            self._tracks = tracks
        if self._tracks_changed_callback is not None:
            self._tracks_changed_callback()

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


def test_subtitle_tracks_exposed_as_dicts(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)
    assert controller.subtitleTracks() == []
    controller.play("http://s/v.mkv")
    assert controller.subtitleTracks() == [{"id": 2, "title": "English"}]


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
