import logging
import os
import sys
import types
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
        self.cache_speed: float | None = None
        self.dwidth: Any = None
        self.dheight: Any = None
        self.sid: Any = "auto"
        self.aid: Any = "auto"
        self.sub_visibility = True
        self.lavfi_complex = ""
        self.lavfi_rejects = False
        self.played: list[str] = []
        self.start = 0.0
        self.http_header_fields: list[str] = []
        self.commands: list[tuple[Any, ...]] = []
        self.seeks: list[tuple[float, str]] = []
        self.terminated = False
        self.observers: list[tuple[str, Any]] = []
        self.event_handlers: dict[str, Any] = {}
        self.track_list = [
            {"id": 1, "type": "video", "title": "v"},
            {"id": 2, "type": "sub", "title": "English"},
            {"id": 3, "type": "sub", "title": "Spanish"},
            {"id": 1, "type": "audio", "lang": "eng", "title": "Surround 5.1"},
            {"id": 2, "type": "audio", "lang": "jpn"},
        ]

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "lavfi_complex" and getattr(self, "lavfi_rejects", False):
            raise RuntimeError("mpv option does not exist")
        object.__setattr__(self, name, value)

    def __setitem__(self, name: str, value: Any) -> None:
        # The options namespace (python-mpv's dict-style access).
        self.__dict__.setdefault("options", {})[name] = value

    def play(self, url: str) -> None:
        self.played.append(url)

    def command(self, *args: Any) -> None:
        self.commands.append(args)

    def seek(self, seconds: float, reference: str = "relative") -> None:
        self.seeks.append((seconds, reference))

    def observe_property(self, name: str, handler: Any) -> None:
        self.observers.append((name, handler))

    def event_callback(self, *event_types: str) -> Any:
        # python-mpv's decorator: called with the event names, returns the
        # registrar that takes the handler.
        def register(handler: Any) -> Any:
            for event_type in event_types:
                self.event_handlers[event_type] = handler
            return handler

        return register

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
    from gravitas.infrastructure.player.track_list import track_label as _track_label

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
        == "German (Forced, SDH)"
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


def test_set_subtitle_track_hides_instead_of_dropping() -> None:
    # "Off" keeps the track selected: dropping it (sid=no) frees its packets,
    # and switching subtitles back on would re-read the stream.
    player, fake = _player()
    player.set_subtitle_track(3)
    assert (fake.sid, fake.sub_visibility) == (3, True)
    assert player.current_subtitle_track() == 3

    player.set_subtitle_track(None)
    assert (fake.sid, fake.sub_visibility) == (3, False)
    assert player.current_subtitle_track() is None

    player.set_subtitle_track(3)
    assert (fake.sid, fake.sub_visibility) == (3, True)


def test_set_audio_track_without_a_graph_sets_aid() -> None:
    player, fake = _player()
    player.set_audio_track(2)
    assert fake.aid == 2
    player.set_audio_track(None)
    assert fake.aid == "no"


def test_prepare_track_switching_holds_every_audio_track_in_the_graph() -> None:
    player, fake = _player()
    fake.aid = 1
    player.prepare_track_switching()
    assert fake.lavfi_complex == "[aid1] anull [ao] ; [aid2] anullsink"
    assert player.current_audio_track() == 1

    # Switching is a graph swap over packets mpv already has: no aid, so no
    # re-read of the stream.
    player.set_audio_track(2)
    assert fake.lavfi_complex == "[aid2] anull [ao] ; [aid1] anullsink"
    assert fake.aid == 1
    assert player.current_audio_track() == 2


def test_the_graph_reselects_the_subtitle_track_it_would_silence() -> None:
    """Installed while the file opens, the graph leaves an embedded subtitle
    track selected but silent until it is selected again."""
    player, fake = _player()
    fake.aid = 1
    fake.sid = 2
    history: list[Any] = []
    real = type(fake).__setattr__

    def record(self: Any, name: str, value: Any) -> None:
        if name == "sid":
            history.append(value)
        real(self, name, value)

    type(fake).__setattr__ = record  # type: ignore[method-assign]
    try:
        player.prepare_track_switching()
    finally:
        type(fake).__setattr__ = real  # type: ignore[method-assign]
    assert history == ["no", 2]
    assert fake.sid == 2


def test_the_graph_leaves_no_subtitle_alone() -> None:
    player, fake = _player()
    fake.aid = 1
    fake.sid = False
    player.prepare_track_switching()
    assert fake.sid is False


def test_prepare_track_switching_is_installed_once_per_file() -> None:
    player, fake = _player()
    fake.aid = 1
    player.prepare_track_switching()
    player.set_audio_track(2)
    player.prepare_track_switching()
    assert fake.lavfi_complex == "[aid2] anull [ao] ; [aid1] anullsink"

    # A graph naming the old file's ids would fail to build against the new
    # one, so loading drops it. A new file comes with its languages; the old
    # file's track id means nothing in it.
    from gravitas.domain.models import TrackLanguages

    player.set_track_languages(TrackLanguages())
    player.play("http://s/other.mkv")
    assert fake.lavfi_complex == ""
    assert fake.aid == "auto"  # mpv chooses again
    fake.aid = 1
    assert player.current_audio_track() == 1  # back to reading aid


def test_prepare_track_switching_skips_files_with_too_many_audio_tracks() -> None:
    player, fake = _player()
    fake.aid = 1
    fake.track_list = [{"id": i, "type": "audio"} for i in range(1, 12)]
    player.prepare_track_switching()
    assert fake.lavfi_complex == ""
    player.set_audio_track(4)
    assert fake.aid == 4


def test_prepare_track_switching_skips_a_single_audio_track() -> None:
    player, fake = _player()
    fake.aid = 1
    fake.track_list = [{"id": 1, "type": "video"}, {"id": 1, "type": "audio"}]
    player.prepare_track_switching()
    assert fake.lavfi_complex == ""


def test_audio_track_falls_back_to_aid_when_the_graph_is_rejected() -> None:
    # A libmpv without lavfi, or one that will not build this graph: switching
    # stays slow, but it must not stop working.
    player, fake = _player()
    fake.aid = 1
    fake.lavfi_rejects = True
    player.prepare_track_switching()
    assert fake.lavfi_complex == ""
    player.set_audio_track(2)
    assert fake.aid == 2
    assert player.current_audio_track() == 2


def test_disabling_audio_removes_the_graph_first() -> None:
    player, fake = _player()
    fake.aid = 1
    player.prepare_track_switching()
    fake.lavfi_rejects = False
    player.set_audio_track(None)
    assert fake.lavfi_complex == ""
    assert fake.aid == "no"


def test_stream_ended_callback_observes_eof_reached() -> None:
    # A dropped connection reaches mpv as end-of-file, never as an error --
    # eof-reached is the only property that moves, and the recovery upstairs
    # hangs off it.
    player, fake = _player()
    calls: list[None] = []
    player.set_stream_ended_callback(lambda: calls.append(None))
    player.set_stream_ended_callback(lambda: calls.append(None))

    observers = [(n, h) for n, h in fake.observers if n == "eof-reached"]
    assert len(observers) == 1
    handler = observers[0][1]

    # It also fires with False on the next load; only the True edge is news.
    handler("eof-reached", False)
    assert calls == []
    handler("eof-reached", True)
    assert len(calls) == 1


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


def test_http_reconnect_covers_a_failed_socket_not_just_a_closed_one() -> None:
    # Without reconnect_on_network_error, ffmpeg turns a read that fails while
    # the network is down into a plain EOF: mpv finishes the file, holds the
    # last frame paused, and nothing retries when the connection returns.
    from gravitas.infrastructure.player.mpv_player import _STREAM_LAVF_OPTIONS

    assert "reconnect_on_network_error=1" in _STREAM_LAVF_OPTIONS
    # mpv splits this option on commas, so no value may contain one.
    assert all("=" in pair for pair in _STREAM_LAVF_OPTIONS.split(","))


def test_reconnect_does_not_break_a_youtube_stream() -> None:
    # reconnect_streamed belongs to the same family and reads like it costs
    # nothing, but it stops every ytdl-resolved stream from opening at all:
    # yt-dlp hands mpv an EDL naming byte ranges into googlevideo, and a
    # reconnect that reissues the request from zero contradicts the range the
    # EDL asked for. ffmpeg answers "End of file" and retries forever, so a
    # trailer never starts. Measured 3/3 with it, 3/3 without.
    from gravitas.infrastructure.player.mpv_player import _STREAM_LAVF_OPTIONS

    assert "reconnect_streamed" not in _STREAM_LAVF_OPTIONS


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


def test_video_size_reads_display_dimensions() -> None:
    player, fake = _player()
    fake.dwidth, fake.dheight = 1920, 800
    assert player.video_size() == (1920, 800)


def test_video_size_is_zero_before_a_frame_decodes() -> None:
    player, _fake = _player()
    assert player.video_size() == (0, 0)


def test_video_size_survives_a_garbage_read() -> None:
    player, fake = _player()
    fake.dwidth, fake.dheight = "wide", 1080
    assert player.video_size() == (0, 0)


def test_dolby_vision_profile_read_from_the_selected_video_track() -> None:
    mpv = FakeMpv()
    mpv.track_list = [
        {"id": 1, "type": "video", "selected": True, "dolby-vision-profile": 5},
        {"id": 2, "type": "audio", "selected": True},
    ]
    player = MpvPlayer(factory=lambda: mpv)
    assert player.video_dolby_vision_profile() == 5


def test_a_track_without_dolby_vision_reports_zero() -> None:
    mpv = FakeMpv()
    mpv.track_list = [{"id": 1, "type": "video", "selected": True}]
    player = MpvPlayer(factory=lambda: mpv)
    assert player.video_dolby_vision_profile() == 0


def test_an_unselected_dolby_vision_track_is_not_what_plays() -> None:
    mpv = FakeMpv()
    mpv.track_list = [
        {"id": 1, "type": "video", "selected": False, "dolby-vision-profile": 5},
        {"id": 2, "type": "video", "selected": True},
    ]
    player = MpvPlayer(factory=lambda: mpv)
    assert player.video_dolby_vision_profile() == 0


class FakeOptions:
    """Stands in for mpv's options namespace, refusing whatever this "build"
    does not have."""

    def __init__(self, *, missing: tuple[str, ...] = ()) -> None:
        self.missing = missing
        self.applied: dict[str, object] = {}

    def __setitem__(self, name: str, value: object) -> None:
        if name in self.missing:
            raise AttributeError("mpv option does not exist")
        self.applied[name] = value


def test_cache_goes_to_disk_and_is_sized_for_it(tmp_path, monkeypatch) -> None:
    """On disk the cache is bounded by free space rather than RAM, so it holds
    minutes in both directions: seeking anywhere already watched is then
    instant and costs no network, which is the whole point of it."""
    from gravitas.infrastructure.player import mpv_player

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    handle = FakeOptions()
    assert mpv_player._apply_cache_options(handle) is True

    assert handle.applied["cache"] == "yes"
    assert handle.applied["cache-on-disk"] == "yes"
    assert str(handle.applied["demuxer-cache-dir"]).startswith(str(tmp_path))
    assert handle.applied["demuxer-max-bytes"] == mpv_player._DISK_MAX_BYTES
    assert handle.applied["demuxer-max-back-bytes"] == mpv_player._DISK_MAX_BACK_BYTES
    # Keeping a gigabyte of played video is only reasonable on disk.
    assert handle.applied["demuxer-max-back-bytes"] > mpv_player._MEMORY_MAX_BACK_BYTES


def test_an_option_this_build_does_not_have_is_not_fatal(tmp_path, monkeypatch) -> None:
    """`cache-dir` was renamed to `demuxer-cache-dir` in mpv 0.41. Passed to
    the constructor, one unknown name took down the whole player with
    "mpv option does not exist" and nothing played at all."""
    from gravitas.infrastructure.player import mpv_player

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    handle = FakeOptions(missing=("demuxer-cache-dir",))

    assert mpv_player._apply_cache_options(handle) is True
    # The older name was tried after it, and everything else still applied.
    assert "cache-dir" in handle.applied
    assert handle.applied["demuxer-max-bytes"] == mpv_player._DISK_MAX_BYTES


def test_a_build_without_the_disk_cache_keeps_memory_sized_limits(tmp_path, monkeypatch) -> None:
    """The sizes follow the placement. Two gigabytes of read-ahead is fine on
    a filesystem and is not fine in RAM."""
    from gravitas.infrastructure.player import mpv_player

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    handle = FakeOptions(missing=("cache-on-disk",))

    assert mpv_player._apply_cache_options(handle) is False
    assert handle.applied["demuxer-max-bytes"] == mpv_player._MEMORY_MAX_BYTES
    assert handle.applied["demuxer-max-back-bytes"] == mpv_player._MEMORY_MAX_BACK_BYTES


def test_an_unusable_cache_directory_falls_back_to_memory(monkeypatch) -> None:
    """A read-only or full cache directory must cost a smaller cache, never a
    player that refuses to start."""
    from gravitas.infrastructure.player import mpv_player

    monkeypatch.setattr(mpv_player, "_disk_cache_dir", lambda: None)
    handle = FakeOptions()

    assert mpv_player._apply_cache_options(handle) is False
    assert "cache-on-disk" not in handle.applied
    assert handle.applied["demuxer-max-bytes"] == mpv_player._MEMORY_MAX_BYTES


def test_a_cache_directory_that_cannot_be_made_is_not_fatal(monkeypatch) -> None:
    from pathlib import Path

    from gravitas.infrastructure.player import mpv_player

    def _refuse(self, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _refuse)
    assert mpv_player._disk_cache_dir() is None


class _EndFile:
    """Stands in for python-mpv's MpvEvent: the reason lives on `.data`."""

    def __init__(self, reason: int) -> None:
        self.data = types.SimpleNamespace(reason=reason)


def test_a_file_that_failed_to_open_reports_it() -> None:
    """The END_FILE error is the only report a refused host produces --
    `eof-reached` never goes true for a file that never played."""
    player, fake = _player()
    failures: list[int] = []
    player.set_load_failed_callback(lambda: failures.append(1))

    fake.event_handlers["end-file"](_EndFile(4))  # MPV_END_FILE_REASON_ERROR

    assert failures == [1]


def test_the_ordinary_ways_a_file_ends_are_not_failures() -> None:
    """Every playback ends with one of these: the file finished, stop() took
    it down, the next source replaced it. Reporting those as dead sources
    would restart playback on the way out of the player."""
    player, fake = _player()
    failures: list[int] = []
    player.set_load_failed_callback(lambda: failures.append(1))

    for reason in (0, 1, 2, 3, 5):  # EOF, RESTARTED, ABORTED, QUIT, REDIRECT
        fake.event_handlers["end-file"](_EndFile(reason))

    assert failures == []


def test_an_event_shape_this_python_mpv_does_not_use_is_not_a_failure() -> None:
    """The reason is read out of whatever the binding hands over -- a struct
    on recent python-mpv, a dict on older ones. An unreadable event reports
    nothing rather than guessing, since guessing wrong here means abandoning a
    source that was playing."""
    player, fake = _player()
    failures: list[int] = []
    player.set_load_failed_callback(lambda: failures.append(1))

    fake.event_handlers["end-file"]({"reason": 4})
    fake.event_handlers["end-file"](object())

    assert failures == [1]


def test_a_file_starts_on_the_short_wait_and_its_first_frame_restores_the_long_one(
    caplog: Any,
) -> None:
    """mpv governs the wait before the first frame and the wait after a stall
    with one option. Four seconds of video buffered before anything shows is
    pure delay on a fresh connection; after a stall it is what stops a minute
    of stutter."""
    from gravitas.infrastructure.player import mpv_player

    player, fake = _player()
    player.play("http://s/v.mkv")
    assert fake.options["cache-pause-wait"] == mpv_player._CACHE_PAUSE_WAIT_START

    with caplog.at_level("INFO", logger="gravitas.infrastructure.player.mpv_player"):
        fake.event_handlers["file-loaded"](None)
        fake.event_handlers["playback-restart"](None)
    assert fake.options["cache-pause-wait"] == mpv_player._CACHE_PAUSE_WAIT
    assert "first frame" in caplog.text and "file open at" in caplog.text

    # A seek later in the same file is not a first frame: no second report.
    caplog.clear()
    with caplog.at_level("INFO", logger="gravitas.infrastructure.player.mpv_player"):
        fake.event_handlers["playback-restart"](None)
    assert "first frame" not in caplog.text

    # The next file starts short again.
    player.play("http://s/next.mkv")
    assert fake.options["cache-pause-wait"] == mpv_player._CACHE_PAUSE_WAIT_START


def test_opening_a_file_is_reported() -> None:
    player, fake = _player()
    opened: list[bool] = []
    player.set_opened_callback(lambda: opened.append(True))
    player.play("http://s/v.mkv")
    fake.event_handlers["file-loaded"](None)
    assert opened == [True]


def test_a_silent_connection_is_timed_out_well_before_mpvs_minute(tmp_path, monkeypatch) -> None:
    from gravitas.infrastructure.player import mpv_player

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    handle = FakeOptions()
    mpv_player._apply_cache_options(handle)
    assert handle.applied["network-timeout"] == mpv_player._NETWORK_TIMEOUT_S
    assert mpv_player._NETWORK_TIMEOUT_S < 60


def test_the_network_timeout_follows_what_mpv_is_reading() -> None:
    """Short on a direct URL, where it is the only guard against a silent
    host; long on the local proxy, which handles upstream silence itself and
    whose 429 backoff must not be cut off (cut at ten seconds, mpv abandoned
    the proxy mid-backoff and reconnected into more 429s)."""
    from gravitas.infrastructure.player import mpv_player

    player, fake = _player()
    player.play("https://cdn.example/file.mkv")
    assert fake.options["network-timeout"] == mpv_player._NETWORK_TIMEOUT_S
    player.play("http://127.0.0.1:45505/token")
    assert fake.options["network-timeout"] == mpv_player._PROXIED_NETWORK_TIMEOUT_S


def test_a_disk_backed_proxy_gets_a_small_cache_and_a_direct_url_the_full_one() -> None:
    """Demuxer options are read when a file opens, so the cache is sized per
    load: behind a proxy that keeps the stream on disk, a second copy in mpv's
    cache is pure waste, and the next direct URL must get the factory's
    settings back rather than inherit the small ones."""
    from gravitas.infrastructure.player import mpv_player

    class Configured(FakeMpv):
        def __init__(self) -> None:
            super().__init__()
            self.options = {
                "cache-on-disk": "yes",
                "demuxer-max-bytes": mpv_player._DISK_MAX_BYTES,
                "demuxer-max-back-bytes": mpv_player._DISK_MAX_BACK_BYTES,
            }

        def __getitem__(self, name: str) -> Any:
            return self.options[name]

    fake = Configured()
    player = MpvPlayer(factory=lambda: fake)

    player.play("http://127.0.0.1:45505/token", upstream_cached=True)
    assert fake.options == {**fake.options, **dict(mpv_player._UPSTREAM_CACHED_OPTIONS)}
    assert fake.options["cache-on-disk"] == "no"

    player.play("https://cdn.example/file.mkv")
    assert fake.options["cache-on-disk"] == "yes"
    assert fake.options["demuxer-max-bytes"] == mpv_player._DISK_MAX_BYTES
    assert fake.options["demuxer-max-back-bytes"] == mpv_player._DISK_MAX_BACK_BYTES


def test_track_languages_wait_for_the_next_file() -> None:
    """Applied at play(), not when set: resetting `aid` on the file that is
    still playing would re-select one of its tracks."""
    from gravitas.domain.models import TrackLanguages

    player, fake = _player()
    fake.aid = 2
    player.set_track_languages(TrackLanguages(audio="de", subtitle="en"))
    assert fake.aid == 2
    assert "alang" not in fake.__dict__.get("options", {})

    player.play("http://s/v.mkv")
    options = fake.__dict__["options"]
    assert options["alang"] == "de,ger,deu"
    assert options["slang"] == "en,eng"
    # A track picked by hand in the last file would otherwise outrank alang.
    assert fake.aid == "auto"
    assert fake.sid == "auto"
    assert fake.sub_visibility is True


def test_a_reload_without_new_languages_keeps_the_current_tracks() -> None:
    from gravitas.domain.models import TrackLanguages

    player, fake = _player()
    player.set_track_languages(TrackLanguages(audio="de"))
    player.play("http://s/v.mkv")
    fake.aid = 1  # the viewer switched tracks
    player.play("http://s/v.mkv", start=120.0)  # a reconnect
    assert fake.aid == 1


def test_subtitles_off_hides_them_and_no_preference_leaves_them_alone() -> None:
    from gravitas.domain.models import TrackLanguages

    player, fake = _player()
    player.set_track_languages(TrackLanguages(subtitle="off"))
    player.play("http://s/v.mkv")
    assert fake.__dict__["options"]["slang"] == ""
    # Hidden, not deselected: turning them on mid-film stays instant.
    assert fake.sub_visibility is False
    assert fake.sid == "auto"

    player.set_track_languages(TrackLanguages())
    player.play("http://s/next.mkv")
    assert fake.__dict__["options"]["alang"] == ""
    assert fake.sub_visibility is False  # as the viewer last left it


def test_a_reload_keeps_an_audio_track_switched_through_the_graph() -> None:
    """A graph switch never writes `aid`, so the reload has to be told."""
    player, fake = _player()
    player.play("http://s/v.mkv")
    fake.aid = 1
    player.prepare_track_switching()
    player.set_audio_track(2)
    player.play("http://s/v.mkv", start=120.0)  # a reconnect
    assert fake.lavfi_complex == ""
    assert fake.aid == 2


def test_ffmpegs_send_us_a_sample_notes_are_debug_not_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from gravitas.infrastructure.player.mpv_player import _mpv_log_handler

    with caplog.at_level(logging.DEBUG, logger="mpv"):
        _mpv_log_handler(
            "warn",
            "ffmpeg/video",
            "h264: Late SEI is not implemented. Update your FFmpeg version to the newest one "
            "from Git. If the problem still occurs, it means that your file has a feature "
            "which has not been implemented.\n",
        )
        _mpv_log_handler(
            "warn",
            "ffmpeg/video",
            "h264: If you want to help, upload a sample of this file to "
            "https://streams.videolan.org/upload/ and contact the ffmpeg-devel mailing list.\n",
        )
        _mpv_log_handler("warn", "ffmpeg", "tcp: Connection refused\n")
    assert [record.levelno for record in caplog.records] == [
        logging.DEBUG,
        logging.DEBUG,
        logging.WARNING,
    ]


def test_chapters_are_read_off_the_chapter_list() -> None:
    player, fake = _player()
    assert player.chapters() == []  # no attribute yet: nothing loaded
    fake.chapter_list = [
        {"title": "Credits", "time": 2492.11},
        {"title": "Intro", "time": 6.01},
        {"time": 0.0},  # untitled keeps its place
        {"title": "broken"},  # no time: not a chapter
    ]
    assert player.chapters() == [(0.0, ""), (6.01, "Intro"), (2492.11, "Credits")]


def _wait_for(condition: Any, timeout: float = 2.0) -> bool:
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_a_demuxer_overflow_takes_the_audio_graph_down_for_the_file() -> None:
    """Holding every audio track selected lets one with a gap (a commentary, a
    partial dub) fill the demuxer queues; in a real session that was followed
    by an audio underrun and a frozen picture. The graph is only an
    optimisation, so on mpv's overflow report it comes down and the playing
    track goes back to plain `aid` -- for the rest of that file."""
    from gravitas.domain.models import TrackLanguages
    from gravitas.infrastructure.player.mpv_player import _mpv_log_handler

    player, fake = _player()
    player.play("http://s/v.mkv")
    fake.aid = 1
    player.prepare_track_switching()
    player.set_audio_track(2)
    assert fake.lavfi_complex != ""

    _mpv_log_handler("warn", "mkv", "Too many packets in the demuxer packet queues:\n")
    assert _wait_for(lambda: fake.lavfi_complex == "")
    assert fake.aid == 2  # still the track the viewer chose
    assert player.current_audio_track() == 2

    # Not put back on this file, even when the track list arrives again.
    player.prepare_track_switching()
    assert fake.lavfi_complex == ""

    # The next file gets its own chance.
    player.set_track_languages(TrackLanguages())
    player.play("http://s/next.mkv")
    fake.aid = 1
    player.prepare_track_switching()
    assert fake.lavfi_complex != ""


def test_an_overflow_without_a_graph_changes_nothing() -> None:
    from gravitas.infrastructure.player.mpv_player import _mpv_log_handler

    player, fake = _player()
    player.play("http://s/v.mkv")
    fake.aid = 1
    _mpv_log_handler("warn", "mkv", "Too many packets in the demuxer packet queues:\n")
    import time

    time.sleep(0.05)
    assert fake.aid == 1
    assert fake.lavfi_complex == ""


def _with_subtitles(tracks: list[dict[str, Any]], preferred: str = "en") -> tuple[Any, Any]:
    from gravitas.domain.models import TrackLanguages

    player, fake = _player()
    fake.track_list = [{"id": 1, "type": "video"}, *tracks]
    player.set_track_languages(TrackLanguages(subtitle=preferred))
    player.play("http://s/v.mkv")
    fake.sid = False  # mpv matched nothing by tag
    return player, fake


def test_an_untagged_track_titled_in_the_preferred_language_is_chosen() -> None:
    """mpv picks subtitles by language TAG; plenty of files leave it out and
    put "English" in the title."""
    player, fake = _with_subtitles(
        [
            {"id": 1, "type": "sub", "lang": "fre", "title": "Français"},
            {"id": 2, "type": "sub", "title": "English (Forced)", "forced": True},
            {"id": 3, "type": "sub", "lang": "und", "title": "English SDH"},
        ]
    )
    assert player.choose_fallback_subtitle() is True
    assert fake.sid == 3  # the full track before the forced-only one
    assert fake.sub_visibility is True


def test_the_only_untagged_track_is_chosen() -> None:
    player, fake = _with_subtitles([{"id": 4, "type": "sub", "title": "Track 1"}])
    assert player.choose_fallback_subtitle() is True
    assert fake.sid == 4


def test_the_fallback_never_overrides_a_fact() -> None:
    # Tagged with another language: that is a fact, the title a guess.
    player, fake = _with_subtitles([{"id": 5, "type": "sub", "lang": "ger", "title": "English?"}])
    assert player.choose_fallback_subtitle() is False
    assert fake.sid is False
    # Something already chosen by tag: left alone.
    player, fake = _with_subtitles([{"id": 6, "type": "sub", "title": "English"}])
    fake.sid = 6
    assert player.choose_fallback_subtitle() is False
    # No preference, or "Off": nothing to fall back to.
    for preferred in ("", "off"):
        player, fake = _with_subtitles([{"id": 7, "type": "sub", "title": "English"}], preferred)
        assert player.choose_fallback_subtitle() is False


def test_the_fallback_waits_for_mpv_when_a_track_is_tagged() -> None:
    """Nothing selected yet with an English-tagged track listed means mpv has
    not chosen yet, not that it found nothing: it will take that track."""
    player, fake = _with_subtitles(
        [
            {"id": 1, "type": "sub", "lang": "eng", "title": "English"},
            {"id": 2, "type": "sub", "title": "English SDH"},
        ]
    )
    assert player.choose_fallback_subtitle() is False
    assert fake.sid is False


def test_whether_the_file_has_the_preferred_language_is_read_off_the_list() -> None:
    def has(tracks: list[dict[str, Any]], preferred: str = "en") -> bool:
        player, _ = _with_subtitles(tracks, preferred)
        return player.has_preferred_subtitle()

    assert has([{"id": 1, "type": "sub", "lang": "eng"}])  # selected or not
    assert has([{"id": 1, "type": "sub", "lang": "en", "title": "SDH"}])
    assert has([{"id": 1, "type": "sub", "lang": "und", "title": "English"}])
    assert not has([{"id": 1, "type": "sub", "lang": "ger", "title": "English?"}])
    # Forced-only shows the foreign lines and little else.
    assert not has([{"id": 1, "type": "sub", "lang": "eng", "forced": True}])
    assert not has([{"id": 1, "type": "sub", "lang": "eng", "title": "English (Forced)"}])
    assert not has([{"id": 1, "type": "sub", "lang": "eng"}], "off")
    assert not has([{"id": 1, "type": "sub", "lang": "eng"}], "")


def test_an_addon_subtitle_is_loaded_beside_the_video() -> None:
    player, fake = _player()
    player.add_subtitle("https://s/en.srt", "English · Release", "eng")
    player.add_subtitle("https://s/es.srt", "Spanish", "spa", select=False)
    assert fake.commands[-2:] == [
        ("sub-add", "https://s/en.srt", "select", "English · Release", "eng"),
        ("sub-add", "https://s/es.srt", "auto", "Spanish", "spa"),
    ]


def test_a_new_file_starts_with_subtitles_in_sync() -> None:
    from gravitas.domain.models import TrackLanguages

    player, fake = _player()
    player.play("http://s/v.mkv")
    player.set_subtitle_delay(1.5)
    assert player.subtitle_delay() == 1.5
    player.play("http://s/v.mkv", start=90)  # a reconnect keeps the correction
    assert fake.sub_delay == 1.5
    player.set_track_languages(TrackLanguages())
    player.play("http://s/next.mkv")  # a new file does not
    assert fake.sub_delay == 0
