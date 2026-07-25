import os
import sys
from pathlib import Path
from typing import Any

import pytest

from gravitas.infrastructure.player.mpv_player import MpvPlayer, _hdr_options


class FakeMpv:
    """Mirrors python-mpv's access model: properties are ATTRIBUTES
    (mpv.time_pos); dict-style access reads/writes the options namespace and
    must not be used for runtime state."""

    def __init__(self) -> None:
        self.pause = False
        self.volume = 100.0
        self.mute = False
        self.time_pos: float | None = None
        self.duration: float | None = None
        self.demuxer_cache_time: float | None = None
        self.sid: Any = "auto"
        self.aid: Any = "auto"
        self.played: list[str] = []
        self.start = 0.0
        self.http_header_fields: list[str] = []
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
    fake.pause = True
    player.play("http://s/v.mkv")
    assert fake.played == ["http://s/v.mkv"]
    assert fake.pause is False


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
    fake.time_pos = 12.5
    fake.duration = 100.0
    assert player.position() == 12.5
    assert player.duration() == 100.0


def test_buffered_to_defaults_to_zero_and_reads_demuxer_cache() -> None:
    player, fake = _player()
    # None before the file opens (and for local files).
    assert player.buffered_to() == 0.0
    fake.demuxer_cache_time = 245.7
    assert player.buffered_to() == 245.7


def test_volume_clamped_and_mute() -> None:
    player, fake = _player()
    player.set_volume(150.0)
    assert fake.volume == 100.0
    player.set_volume(-5.0)
    assert fake.volume == 0.0
    player.set_muted(True)
    assert player.is_muted() is True


def test_subtitle_tracks_lists_only_subs() -> None:
    player, _ = _player()
    assert player.subtitle_tracks() == [(2, "English"), (3, "Spanish")]


def test_audio_tracks_labelled_with_language() -> None:
    player, _ = _player()
    assert player.audio_tracks() == [(1, "English · Surround 5.1"), (2, "Japanese")]


def test_track_label_details() -> None:
    from gravitas.infrastructure.player.mpv_player import _track_label

    # title already names the language -> no redundant code prefix
    assert (
        _track_label({"id": 1, "type": "sub", "lang": "en-US", "title": "English (United States)"})
        == "English (United States)"
    )
    # regioned code expands, region kept
    assert _track_label({"id": 1, "type": "sub", "lang": "fr-CA"}) == "French (CA)"
    # flags surface
    assert (
        _track_label(
            {"id": 1, "type": "sub", "lang": "de", "forced": True, "hearing-impaired": True}
        )
        == "German — Forced, SDH"
    )
    # audio channel count appended unless the title already says it
    assert (
        _track_label(
            {
                "id": 1,
                "type": "audio",
                "lang": "en",
                "title": "TrueHD Atmos",
                "demux-channel-count": 8,
            }
        )
        == "English · TrueHD Atmos · 7.1"
    )
    assert (
        _track_label(
            {
                "id": 1,
                "type": "audio",
                "lang": "en",
                "title": "Dolby Digital 5.1",
                "demux-channel-count": 6,
            }
        )
        == "English · Dolby Digital 5.1"
    )
    # unknown code passes through
    assert _track_label({"id": 4, "type": "sub", "lang": "tlh"}) == "tlh"


def test_identical_track_labels_get_indexed() -> None:
    fake = FakeMpv()
    fake.track_list = [
        {"id": 2, "type": "sub", "lang": "en-US", "title": "English (United States)"},
        {"id": 3, "type": "sub", "lang": "en-US", "title": "English (United States)"},
        {"id": 4, "type": "sub", "lang": "da"},
    ]
    player = MpvPlayer(factory=lambda: fake)
    assert player.subtitle_tracks() == [
        (2, "English (United States) · #1"),
        (3, "English (United States) · #2"),
        (4, "Danish"),
    ]


def test_set_subtitle_track() -> None:
    player, fake = _player()
    player.set_subtitle_track(3)
    assert fake.sid == 3
    player.set_subtitle_track(None)
    assert fake.sid == "no"


def test_set_audio_track() -> None:
    player, fake = _player()
    player.set_audio_track(2)
    assert fake.aid == 2
    player.set_audio_track(None)
    assert fake.aid == "no"


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

    # The always-on hwdec-current decode-path diagnostic also registers at
    # construction; this test only cares about the track-list observer.
    track_observers = [(n, h) for n, h in fake.observers if n == "track-list"]
    assert len(track_observers) == 1
    _, handler = track_observers[0]

    handler("track-list", [])
    assert calls == [None]


def test_set_tracks_changed_callback_registers_observer_only_once() -> None:
    player, fake = _player()
    player.set_tracks_changed_callback(lambda: None)
    player.set_tracks_changed_callback(lambda: None)

    assert [n for n, _ in fake.observers].count("track-list") == 1


def test_state_callback_observes_pause_duration_mute_once() -> None:
    player, fake = _player()
    calls: list[None] = []
    player.set_state_changed_callback(lambda: calls.append(None))
    player.set_state_changed_callback(lambda: calls.append(None))

    # Ignore the construction-time hwdec-current diagnostic observer.
    names = [n for n, _ in fake.observers if n != "hwdec-current"]
    assert names == ["pause", "duration", "mute"]

    pause_handler = next(h for n, h in fake.observers if n == "pause")
    pause_handler("pause", True)
    assert len(calls) == 1


def test_apply_subtitle_style_maps_mpv_options() -> None:
    from gravitas.domain.models import SubtitleStyle

    player, fake = _player()
    player.apply_subtitle_style(
        SubtitleStyle(font_size=70, color="#FFE400", border_size=1, back_opacity=50, bold=True)
    )
    assert fake.sub_font_size == 70
    assert fake.sub_color == "#FFE400"
    assert fake.sub_border_size == 1
    assert fake.sub_back_color == "#80000000"
    assert fake.sub_bold is True


def test_libmpv_discovery_extends_dyld_path_on_macos() -> None:
    from gravitas.infrastructure.player.mpv_player import (
        _ensure_libmpv_discoverable,
    )

    env: dict[str, str] = {}
    _ensure_libmpv_discoverable(env, platform="darwin")
    parts = env["DYLD_FALLBACK_LIBRARY_PATH"].split(":")
    assert parts[0] == "/opt/homebrew/lib"
    assert "/usr/lib" in parts  # dyld defaults preserved

    # existing value extended, not clobbered
    env2 = {"DYLD_FALLBACK_LIBRARY_PATH": "/custom/lib"}
    _ensure_libmpv_discoverable(env2, platform="darwin")
    assert "/custom/lib" in env2["DYLD_FALLBACK_LIBRARY_PATH"]
    assert "/opt/homebrew/lib" in env2["DYLD_FALLBACK_LIBRARY_PATH"]

    # non-macOS untouched
    env3: dict[str, str] = {}
    _ensure_libmpv_discoverable(env3, platform="linux")
    assert env3 == {}


def test_play_sets_start_option_for_resume() -> None:
    player, fake = _player()
    player.play("http://s/v.mkv", start=1820.5)
    # mpv applies `start` at load time, so resume needs no seek-after-load.
    assert fake.start == 1820.5
    assert fake.played == ["http://s/v.mkv"]


def test_play_from_zero_clears_start_option() -> None:
    player, fake = _player()
    player.play("http://s/v.mkv", start=900.0)
    player.play("http://s/other.mkv")
    # A stale `start` would silently seek the NEXT file to the old position.
    assert fake.start == 0


def test_play_sets_proxy_headers_for_mpv() -> None:
    mpv = FakeMpv()
    player = MpvPlayer(factory=lambda: mpv)
    player.play("https://cdn/v.mp4", headers=(("Referer", "https://origin/"),))
    assert mpv.http_header_fields == ["Referer: https://origin/"]


def test_play_clears_headers_from_a_previous_stream() -> None:
    # mpv keeps http-header-fields across loads: a stale Referer would leak
    # onto the next stream and can itself cause a 403.
    mpv = FakeMpv()
    player = MpvPlayer(factory=lambda: mpv)
    player.play("https://cdn/a.mp4", headers=(("Referer", "https://origin/"),))
    player.play("https://cdn/b.mp4")
    assert mpv.http_header_fields == []


def test_libmpv_override_goes_on_path_on_windows() -> None:
    # python-mpv opens the DLL by name off %PATH% at import time, so a manual
    # libmpv is reachable only if its directory is on PATH before that import.
    from gravitas.infrastructure.player.mpv_player import _ensure_libmpv_discoverable

    # Host-shaped paths on purpose: os.path.dirname and os.pathsep follow the
    # machine running the test, and it is the naming rules that differ on
    # Windows, not the splitting.
    library = os.path.join("opt", "mpv", "libmpv-2.dll")
    env = {"GRAVITAS_LIBMPV": library, "PATH": "existing-dir"}
    _ensure_libmpv_discoverable(env, platform="win32")
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(library)
    assert "existing-dir" in env["PATH"].split(os.pathsep)


def test_windows_without_an_override_touches_nothing() -> None:
    # The bundle puts its own DLL on PATH later; a dev checkout is expected to
    # have one on PATH already. Neither wants a spurious entry here.
    from gravitas.infrastructure.player.mpv_player import _ensure_libmpv_discoverable

    env = {"PATH": "existing-dir"}
    _ensure_libmpv_discoverable(env, platform="win32")
    assert env == {"PATH": "existing-dir"}


def test_libmpv_override_redirects_find_library_on_unix(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes.util

    from gravitas.infrastructure.player.mpv_player import _ensure_libmpv_discoverable

    # Snapshot so the patch this installs is undone with the test.
    monkeypatch.setattr(ctypes.util, "find_library", ctypes.util.find_library)
    _ensure_libmpv_discoverable({"GRAVITAS_LIBMPV": "/opt/mpv/libmpv.so.2"}, platform="linux")
    assert ctypes.util.find_library("mpv") == "/opt/mpv/libmpv.so.2"
    # Every other lookup still goes to the real implementation.
    assert ctypes.util.find_library("definitely-not-a-library-12345") is None


def test_bundled_libmpv_goes_on_path_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gravitas.infrastructure.player.mpv_player import _ensure_bundled_libmpv_findable

    (tmp_path / "libmpv-2.dll").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    env = {"PATH": "existing-dir"}
    _ensure_bundled_libmpv_findable(env, platform="win32")
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path)


def test_bundled_libmpv_is_a_noop_when_the_dll_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gravitas.infrastructure.player.mpv_player import _ensure_bundled_libmpv_findable

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    env = {"PATH": "existing-dir"}
    _ensure_bundled_libmpv_findable(env, platform="win32")
    assert env == {"PATH": "existing-dir"}


def test_bundled_libmpv_is_a_noop_outside_a_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    from gravitas.infrastructure.player.mpv_player import _ensure_bundled_libmpv_findable

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    env: dict[str, str] = {}
    _ensure_bundled_libmpv_findable(env, platform="win32")
    assert env == {}


def test_bundled_ytdlp_uses_the_windows_executable_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mpv's ytdl_hook spawns `yt-dlp`, which Windows resolves to yt-dlp.exe --
    # looking for the extension-less name there finds nothing and YouTube and
    # every trailer silently stop working.
    from gravitas.infrastructure.player.mpv_player import _ensure_bundled_ytdlp_on_path

    (tmp_path / "yt-dlp.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    env = {"PATH": "existing-dir"}
    _ensure_bundled_ytdlp_on_path(env, platform="win32")
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path)

    # The Unix name is not the Windows one: a bundle carrying only the .exe
    # must not put itself on PATH on Linux.
    unix_env = {"PATH": "/usr/bin"}
    _ensure_bundled_ytdlp_on_path(unix_env, platform="linux")
    assert unix_env == {"PATH": "/usr/bin"}


def test_path_prepend_never_duplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Both the libmpv and the yt-dlp hook point at _MEIPASS; running them in
    # sequence must not grow PATH by an entry each launch.
    from gravitas.infrastructure.player.mpv_player import (
        _ensure_bundled_libmpv_findable,
        _ensure_bundled_ytdlp_on_path,
    )

    (tmp_path / "libmpv-2.dll").write_bytes(b"")
    (tmp_path / "yt-dlp.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    env = {"PATH": "existing-dir"}
    _ensure_bundled_libmpv_findable(env, platform="win32")
    _ensure_bundled_ytdlp_on_path(env, platform="win32")
    assert env["PATH"].split(os.pathsep) == [str(tmp_path), "existing-dir"]


def test_an_explicit_libmpv_beats_the_bundled_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Setting GRAVITAS_LIBMPV inside a bundle is how you test a different
    # libmpv against a shipped build; the bundled DLL taking it back would
    # make the variable look broken.
    from gravitas.infrastructure.player.mpv_player import (
        _ensure_bundled_libmpv_findable,
        _ensure_libmpv_discoverable,
    )

    (tmp_path / "libmpv-2.dll").write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    override = os.path.join("elsewhere", "libmpv-2.dll")
    env = {"GRAVITAS_LIBMPV": override, "PATH": "existing-dir"}
    _ensure_libmpv_discoverable(env, platform="win32")
    _ensure_bundled_libmpv_findable(env, platform="win32")
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(override)


def test_hdr_options_are_absent_unless_hdr_is_asked_for() -> None:
    assert _hdr_options(None) == {}


def test_hdr10_hands_mpv_the_encoding_the_swapchain_wants() -> None:
    options = _hdr_options("hdr10")
    assert options["target_prim"] == "bt.2020"
    assert options["target_trc"] == "pq"
    # Dithering to 8 is mpv's default assumption about an ordinary display and
    # would throw away exactly the precision the 10-bit surface exists to carry.
    assert options["dither_depth"] == 10


def test_scrgb_asks_for_linear_light_and_no_dither() -> None:
    options = _hdr_options("scrgb")
    assert options["target_prim"] == "bt.709"
    assert options["target_trc"] == "linear"
    # A float surface has nothing to dither to.
    assert options["dither_depth"] == "no"


def test_hdr_never_pins_the_peak() -> None:
    # mpv reads the display's real capability; a hardcoded number here would
    # override a better answer.
    for mode in ("hdr10", "scrgb"):
        assert "target_peak" not in _hdr_options(mode)
