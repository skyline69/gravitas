from typing import Any

from gravitas.infrastructure.player.mpv_player import MpvPlayer


class FakeMpv:
    def __init__(self) -> None:
        self.props: dict[str, Any] = {"pause": False, "volume": 100.0, "mute": False}
        self.played: list[str] = []
        self.commands: list[tuple[Any, ...]] = []
        self.seeks: list[tuple[float, str]] = []
        self.terminated = False
        self.observers: list[tuple[str, Any]] = []
        self.track_list = [
            {"id": 1, "type": "video", "title": "v"},
            {"id": 2, "type": "sub", "title": "English"},
            {"id": 3, "type": "sub", "title": "Spanish"},
            {"id": 1, "type": "audio", "lang": "eng", "title": "Surround 5.1"},
            {"id": 2, "type": "audio", "lang": "jpn"},
        ]

    def play(self, url: str) -> None:
        self.played.append(url)

    def command(self, *args: Any) -> None:
        self.commands.append(args)

    def seek(self, seconds: float, reference: str = "relative") -> None:
        self.seeks.append((seconds, reference))

    def __setitem__(self, key: str, value: Any) -> None:
        self.props[key] = value

    def __getitem__(self, key: str) -> Any:
        return self.props[key]

    def __getattr__(self, name: str) -> Any:
        if name == "track_list":
            return object.__getattribute__(self, "__dict__")["track_list"]
        raise AttributeError(name)

    def observe_property(self, name: str, handler: Any) -> None:
        self.observers.append((name, handler))

    def terminate(self) -> None:
        self.terminated = True


def _player() -> tuple[MpvPlayer, FakeMpv]:
    fake = FakeMpv()
    player = MpvPlayer(factory=lambda: fake)
    return player, fake


def test_play_forwards_url_and_unpauses() -> None:
    player, fake = _player()
    fake.props["pause"] = True
    player.play("http://s/v.mkv")
    assert fake.played == ["http://s/v.mkv"]
    assert fake.props["pause"] is False


def test_stop_issues_command() -> None:
    player, fake = _player()
    player.stop()
    assert fake.commands == [("stop",)]


def test_pause_state_roundtrip() -> None:
    player, _fake = _player()
    player.pause()
    assert player.is_paused() is True
    player.resume()
    assert player.is_paused() is False


def test_seek_absolute() -> None:
    player, fake = _player()
    player.seek(90.0)
    assert fake.seeks == [(90.0, "absolute")]


def test_position_and_duration_default_to_zero() -> None:
    player, fake = _player()
    assert player.position() == 0.0
    assert player.duration() == 0.0
    fake.props["time-pos"] = 12.5
    fake.props["duration"] = 100.0
    assert player.position() == 12.5
    assert player.duration() == 100.0


def test_volume_clamped_and_mute() -> None:
    player, fake = _player()
    player.set_volume(150.0)
    assert fake.props["volume"] == 100.0
    player.set_volume(-5.0)
    assert fake.props["volume"] == 0.0
    player.set_muted(True)
    assert player.is_muted() is True


def test_subtitle_tracks_lists_only_subs() -> None:
    player, _ = _player()
    assert player.subtitle_tracks() == [(2, "English"), (3, "Spanish")]


def test_audio_tracks_labelled_with_language() -> None:
    player, _ = _player()
    assert player.audio_tracks() == [(1, "eng · Surround 5.1"), (2, "jpn")]


def test_set_subtitle_track() -> None:
    player, fake = _player()
    player.set_subtitle_track(3)
    assert fake.props["sid"] == 3
    player.set_subtitle_track(None)
    assert fake.props["sid"] == "no"


def test_set_audio_track() -> None:
    player, fake = _player()
    player.set_audio_track(2)
    assert fake.props["aid"] == 2
    player.set_audio_track(None)
    assert fake.props["aid"] == "no"


def test_render_handle_exposes_mpv() -> None:
    player, fake = _player()
    assert player.render_handle() is fake


def test_shutdown_terminates() -> None:
    player, fake = _player()
    player.shutdown()
    assert fake.terminated is True


def test_set_tracks_changed_callback_registers_observer_and_invokes_callback() -> None:
    player, fake = _player()
    calls: list[None] = []
    player.set_tracks_changed_callback(lambda: calls.append(None))

    assert len(fake.observers) == 1
    name, handler = fake.observers[0]
    assert name == "track-list"

    handler("track-list", [])
    assert calls == [None]


def test_set_tracks_changed_callback_registers_observer_only_once() -> None:
    player, fake = _player()
    player.set_tracks_changed_callback(lambda: None)
    player.set_tracks_changed_callback(lambda: None)

    assert len(fake.observers) == 1


def test_state_callback_observes_pause_duration_mute_once() -> None:
    player, fake = _player()
    calls: list[None] = []
    player.set_state_changed_callback(lambda: calls.append(None))
    player.set_state_changed_callback(lambda: calls.append(None))

    names = [n for n, _ in fake.observers]
    assert names == ["pause", "duration", "mute"]

    fake.observers[0][1]("pause", True)
    assert len(calls) == 1
