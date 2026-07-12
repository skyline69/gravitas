from typing import Any

from gravitas.infrastructure.player.mpv_player import MpvPlayer


class FakeMpv:
    def __init__(self) -> None:
        self.props: dict[str, Any] = {}
        self.played: list[str] = []
        self.terminated = False
        self.track_list = [
            {"id": 1, "type": "video", "title": "v"},
            {"id": 2, "type": "sub", "title": "English"},
            {"id": 3, "type": "sub", "title": "Spanish"},
        ]

    def play(self, url: str) -> None:
        self.played.append(url)

    def __setitem__(self, key: str, value: Any) -> None:
        self.props[key] = value

    def __getattr__(self, name: str) -> Any:
        if name == "track_list":
            return object.__getattribute__(self, "__dict__")["track_list"]
        raise AttributeError(name)

    def terminate(self) -> None:
        self.terminated = True


def _player() -> tuple[MpvPlayer, FakeMpv]:
    fake = FakeMpv()
    player = MpvPlayer(window_id=42, factory=lambda wid: fake)
    return player, fake


def test_play_forwards_url() -> None:
    player, fake = _player()
    player.play("http://s/v.mkv")
    assert fake.played == ["http://s/v.mkv"]


def test_subtitle_tracks_lists_only_subs() -> None:
    player, _ = _player()
    assert player.subtitle_tracks() == [(2, "English"), (3, "Spanish")]


def test_set_subtitle_track() -> None:
    player, fake = _player()
    player.set_subtitle_track(3)
    assert fake.props["sid"] == 3
    player.set_subtitle_track(None)
    assert fake.props["sid"] == "no"


def test_shutdown_terminates() -> None:
    player, fake = _player()
    player.shutdown()
    assert fake.terminated is True
