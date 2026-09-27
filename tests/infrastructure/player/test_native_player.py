"""NativePlayer against a fake engine: the adapter's own logic, no Rust."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gravitas.domain.models import TrackLanguages
from gravitas.domain.ports import MediaPlayer
from gravitas.infrastructure.player import native_player
from gravitas.infrastructure.player.native_player import (
    EventCallback,
    NativePlayer,
    hardware_decoding,
)

TRACKS: list[dict[str, Any]] = [
    {"id": 1, "type": "video", "codec": "hevc", "selected": True},
    {"id": 1, "type": "audio", "lang": "eng", "demux-channel-count": 6, "selected": True},
    {"id": 2, "type": "audio", "lang": "ger", "demux-channel-count": 2, "selected": False},
    {"id": 1, "type": "sub", "lang": "eng", "forced": True, "selected": False},
    # Untagged: the engine drops "und", as mpv does.
    {"id": 2, "type": "sub", "title": "English SDH", "selected": False},
]


class FakeEngine:
    def __init__(self, on_event: EventCallback) -> None:
        self.emit = on_event
        self.loads: list[dict[str, Any]] = []
        self.tracks_list = [dict(t) for t in TRACKS]
        self.chosen: dict[str, int | None] = {"video": 1, "audio": 1, "sub": None}
        self.selects: list[tuple[str, int | None]] = []
        self.paused = False
        self.size: tuple[int, int] | None = (1920, 1080)
        self.shut = False
        self.visible = True
        self.delay = 0.0
        self.style: dict[str, Any] = {}
        self.added: list[tuple[str, dict[str, Any]]] = []
        self.gpu = True
        self.gpu_ready = False

    def set_gpu_rendering(self, enabled: bool) -> None:
        self.gpu = enabled

    def renders_dolby_vision(self) -> bool:
        return self.gpu and self.gpu_ready

    def load(self, url: str, **options: Any) -> None:
        self.loads.append({"url": url, **options})
        self.delay = 0.0  # the engine resets it with every load

    def set_subtitle_visible(self, visible: bool) -> None:
        self.visible = visible

    def subtitle_visible(self) -> bool:
        return self.visible

    def set_subtitle_delay(self, seconds: float) -> None:
        self.delay = seconds

    def subtitle_delay(self) -> float:
        return self.delay

    def set_subtitle_style(self, **style: Any) -> None:
        self.style = style

    def add_subtitle(self, url: str, **options: Any) -> int:
        if url.endswith(".broken"):
            raise RuntimeError("Invalid data found when processing input")
        self.added.append((url, options))
        return 3

    def stop(self) -> None: ...
    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def is_paused(self) -> bool:
        return self.paused

    def seek(self, seconds: float) -> None: ...
    def position(self) -> float:
        return 12.0

    def duration(self) -> float | None:
        return None

    def set_volume(self, volume: float) -> None: ...
    def volume(self) -> float:
        return 100.0

    def set_muted(self, muted: bool) -> None: ...
    def is_muted(self) -> bool:
        return False

    def is_loading(self) -> bool:
        return False

    def tracks(self) -> list[dict[str, Any]]:
        return [
            {**t, "selected": self.chosen.get(str(t["type"])) == t["id"]} for t in self.tracks_list
        ]

    def select(self, kind: str, id: int | None) -> None:
        self.selects.append((kind, id))
        self.chosen[kind] = id

    def selected(self, kind: str) -> int | None:
        return self.chosen.get(kind)

    def chapters(self) -> list[tuple[float, str]]:
        return [(600.0, "Credits"), (0.0, "Intro")]

    def buffered_to(self) -> float | None:
        return None

    def read_rate(self) -> float:
        return 1234.0

    def network_rate(self) -> float | None:
        return self.network

    def dropped_frames(self) -> int:
        return 7

    def video_size(self) -> tuple[int, int] | None:
        return self.size

    network: float | None = None

    def set_hardware_decoding(self, mode: str) -> None:
        self.hwdec = mode

    def use_shared_device(self, device: Any) -> None:
        self.shared_device = device

    def shutdown(self) -> None:
        self.shut = True


def _player(
    environ: dict[str, str] | None = None, shared_device: Any = None
) -> tuple[NativePlayer, FakeEngine]:
    engines: list[FakeEngine] = []

    def factory(on_event: EventCallback) -> FakeEngine:
        engines.append(FakeEngine(on_event))
        return engines[-1]

    player = NativePlayer(factory, environ=environ or {}, shared_device=shared_device)
    return player, engines[0]


def test_it_is_a_media_player() -> None:
    player, _ = _player()
    assert isinstance(player, MediaPlayer)


def test_a_new_file_carries_the_language_preference() -> None:
    player, engine = _player()
    player.set_track_languages(TrackLanguages(audio="de", subtitle="en"))
    player.play("https://cdn.example/film.mkv", start=-3, headers=[("Referer", "x")])
    load = engine.loads[-1]
    assert load["start"] == 0.0
    assert load["headers"] == [("Referer", "x")]
    assert load["audio_languages"][:1] == ["de"] and "ger" in load["audio_languages"]
    assert "eng" in load["subtitle_languages"]
    assert load["network_timeout"] == 10.0


def test_a_resume_starts_at_the_keyframe_and_a_new_file_at_zero() -> None:
    player, engine = _player()
    player.play("https://cdn.example/a.mkv")
    assert engine.loads[-1]["keyframe_start"] is False
    player.play("https://cdn.example/a.mkv", start=600)
    assert engine.loads[-1]["keyframe_start"] is True


def test_the_local_proxy_gets_the_longer_timeout() -> None:
    player, engine = _player()
    player.play("http://127.0.0.1:4321/stream")
    assert engine.loads[-1]["network_timeout"] == 60.0


def test_subtitles_off_asks_for_none() -> None:
    player, engine = _player()
    player.set_track_languages(TrackLanguages(audio="en", subtitle="off"))
    player.play("https://cdn.example/a.mkv")
    assert engine.loads[-1]["subtitle_languages"] == []
    assert player.current_subtitle_track() is None


def test_a_reload_comes_back_on_the_viewers_tracks() -> None:
    """A play() with no preference before it is a reconnect: the tracks the
    viewer switched to return once the engine has the file open."""
    player, engine = _player()
    player.set_track_languages(TrackLanguages(audio="en", subtitle="en"))
    player.play("https://cdn.example/a.mkv")
    player.set_audio_track(2)
    player.play("https://cdn.example/a.mkv", start=600)
    assert engine.loads[-1]["audio_languages"] == []
    engine.chosen["audio"] = 1  # the reload opens on the default again
    engine.emit("file-loaded", None)
    assert engine.chosen["audio"] == 2


def test_engine_events_reach_the_ports_callbacks() -> None:
    player, engine = _player()
    fired: list[str] = []
    player.set_tracks_changed_callback(lambda: fired.append("tracks"))
    player.set_state_changed_callback(lambda: fired.append("state"))
    player.set_stream_ended_callback(lambda: fired.append("ended"))
    player.set_opened_callback(lambda: fired.append("opened"))
    player.set_load_failed_callback(lambda: fired.append("failed"))
    for name, value in (
        ("tracks-changed", None),
        ("state-changed", None),
        ("end-of-file", None),
        ("file-loaded", None),
        ("load-failed", "Connection refused"),
        ("first-frame", 812),
    ):
        engine.emit(name, value)
    assert fired == ["tracks", "state", "ended", "opened", "failed"]


def test_tracks_are_labelled_like_mpvs() -> None:
    player, _ = _player()
    assert player.audio_tracks() == [(1, "English · 5.1"), (2, "German · Stereo")]
    assert player.subtitle_tracks() == [(1, "English (Forced)"), (2, "English SDH")]


def test_subtitles_off_hides_the_track_and_on_brings_it_back() -> None:
    player, engine = _player()
    player.set_subtitle_track(2)
    assert player.current_subtitle_track() == 2
    player.set_subtitle_track(None)
    assert player.current_subtitle_track() is None
    assert engine.chosen["sub"] == 2  # still selected, only hidden
    player.set_subtitle_track(2)
    assert engine.selects.count(("sub", 2)) == 1  # no second switch


def test_the_untagged_english_track_is_the_fallback() -> None:
    player, engine = _player()
    player.set_track_languages(TrackLanguages(audio="en", subtitle="en"))
    player.play("https://cdn.example/a.mkv")
    # Only a forced track is tagged English, so the file has no full one by
    # tag; the untagged one titled "English SDH" is.
    assert player.has_preferred_subtitle()
    # Without the forced one, the title is all there is to go on.
    engine.tracks_list = [t for t in engine.tracks_list if t["type"] != "sub" or t["id"] != 1]
    assert player.choose_fallback_subtitle()
    assert engine.chosen["sub"] == 2


def test_what_is_playing() -> None:
    player, _ = _player()
    assert player.chapters() == [(0.0, "Intro"), (600.0, "Credits")]
    report = player.decode_report()
    assert (report.codec, report.height, report.dropped_frames) == ("hevc", 1080, 7)
    assert player.download_speed() == 1234.0
    assert player.buffered_to() == 0.0
    assert player.duration() == 0.0
    assert player.video_dolby_vision_profile() == 0


def test_an_addon_subtitle_file_goes_to_the_engine() -> None:
    player, engine = _player()
    player.add_subtitle("https://subs.example/a.srt", "English · A", "eng", select=False)
    assert engine.added == [
        ("https://subs.example/a.srt", {"title": "English · A", "language": "eng", "select": False})
    ]


def test_an_unreadable_subtitle_file_is_a_playback_failure() -> None:
    from gravitas.domain.errors import PlaybackFailed

    player, _ = _player()
    with pytest.raises(PlaybackFailed):
        player.add_subtitle("https://subs.example/a.broken", "English", "eng")


def test_the_viewers_style_reaches_the_engine() -> None:
    from gravitas.domain.models import SubtitleStyle

    player, engine = _player()
    player.apply_subtitle_style(SubtitleStyle(font_size=40, color="#FFD700", back_opacity=60))
    assert engine.style == {
        "font_size": 40.0,
        "color": 0xFFD700,
        "border_size": 3.0,
        "back_opacity": 60.0,
        "bold": False,
    }
    player.apply_subtitle_style(SubtitleStyle(color="not a colour"))
    assert engine.style["color"] == 0xFFFFFF


def test_the_subtitle_delay_resets_for_a_new_file_only() -> None:
    player, _ = _player()
    player.set_subtitle_delay(1.5)
    player.play("https://cdn.example/a.mkv")  # a reload keeps it
    assert player.subtitle_delay() == 1.5
    player.set_track_languages(TrackLanguages())
    player.play("https://cdn.example/b.mkv")
    assert player.subtitle_delay() == 0.0


def test_shutdown_reaches_the_engine() -> None:
    player, engine = _player()
    player.shutdown()
    assert engine.shut


@pytest.mark.parametrize(
    ("setting", "value", "wanted"),
    [
        ("native", "", True),
        ("mpv", "", False),
        # The environment overrides the setting, both ways.
        ("mpv", " Native ", True),
        ("native", "mpv", False),
        # Anything else is not an override.
        ("mpv", "something", False),
    ],
)
def test_the_setting_chooses_the_engine_unless_the_environment_does(
    setting: str, value: str, wanted: bool
) -> None:
    assert native_player.requested(setting, {"GRAVITAS_PLAYER": value}) is wanted


def test_a_missing_module_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(name: str, package: str | None = None) -> object:
        raise ImportError(name)

    monkeypatch.setattr(native_player.importlib, "import_module", refuse)
    assert native_player.available() is False
    from gravitas.domain.errors import PlaybackFailed

    with pytest.raises(PlaybackFailed):
        NativePlayer()


def test_a_new_load_plays_even_if_the_last_one_was_paused() -> None:
    """The engine keeps its pause across loads, as mpv does; MpvPlayer.play()
    unpauses and so must this, or the next episode opens paused."""
    player, _ = _player()
    player.play("https://cdn.example/a.mkv")
    player.pause()
    player.play("https://cdn.example/b.mkv")
    assert not player.is_paused()


def test_dolby_vision_is_reported_only_while_the_engine_cannot_convert_it() -> None:
    """The port's callers warn about -- and remember -- releases that play in
    the wrong colours. Through libplacebo profile 5 renders correctly, so the
    profile is only reported while frames go through swscale."""
    player, engine = _player()
    engine.tracks_list[0]["dolby-vision-profile"] = 5
    assert player.video_dolby_vision_profile() == 5  # renderer not up yet
    engine.gpu_ready = True
    assert player.video_dolby_vision_profile() == 0
    engine.tracks_list[0]["dolby-vision-profile"] = None
    engine.gpu_ready = False
    assert player.video_dolby_vision_profile() == 0


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        ("", "auto"),
        ("auto-safe", "auto"),
        ("auto-copy", "auto"),
        ("no", "no"),
        ("nvdec", "cuda"),
        ("nvdec-copy", "cuda"),
        ("vaapi", "vaapi"),
        ("Vulkan-Copy", "vulkan"),
        ("videotoolbox", "videotoolbox"),
    ],
)
def test_gravitas_hwdec_speaks_mpvs_names(value: str, mode: str) -> None:
    assert hardware_decoding({"GRAVITAS_HWDEC": value}) == mode


def test_hardware_decoding_is_left_alone_unless_asked() -> None:
    _, engine = _player({})
    assert not hasattr(engine, "hwdec")
    _, engine = _player({"GRAVITAS_HWDEC": "no"})
    assert engine.hwdec == "no"


class FakeSharedDevice:
    decodes = True

    def handles(self) -> dict[str, int]:
        return {"physical_device": 1, "device": 2, "queue_family": 0, "queue_index": 0}


def test_the_window_s_device_is_handed_to_the_engine() -> None:
    device = FakeSharedDevice()
    _, engine = _player(shared_device=device)
    assert engine.shared_device is device
    _, engine = _player()
    assert not hasattr(engine, "shared_device")


@pytest.mark.parametrize(
    ("value", "wanted"),
    [("", True), ("auto-safe", True), ("vulkan-copy", True), ("no", False), ("nvdec", False)],
)
def test_a_shared_device_is_made_only_when_it_may_decode(value: str, wanted: bool) -> None:
    assert native_player.wants_shared_device({"GRAVITAS_HWDEC": value}) is wanted


def test_a_device_the_engine_cannot_make_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class Module:
        class SharedDevice:
            def __init__(self, **_handles: int) -> None:
                raise RuntimeError("GPU rendering: libplacebo could not create a Vulkan device")

    monkeypatch.setattr(native_player.importlib, "import_module", lambda _name: Module)
    assert native_player.create_shared_device(1, 2) is None


def test_software_rendering_can_be_asked_for() -> None:
    _, engine = _player({"GRAVITAS_NATIVE_RENDERER": "software"})
    assert engine.gpu is False
    _, engine = _player({})
    assert engine.gpu is True


def test_the_line_s_rate_comes_off_the_network_while_the_engine_reads_it() -> None:
    player, engine = _player()
    assert player.download_speed() == 1234.0  # FFmpeg reads it: its own rate
    engine.network = 5000.0
    assert player.download_speed() == 5000.0
    # Nothing fetched lately is no sample -- never the local cache's rate.
    engine.network = 0.0
    assert player.download_speed() == 0.0


class FakeNetwork:
    def __init__(self) -> None:
        self.settings: list[tuple[bool, str | None]] = []
        self.keep_dirs: list[str | None] = []

    def configure_network(
        self, *, parallel: bool, cache_dir: str | None = None, keep_dir: str | None = None
    ) -> None:
        self.settings.append((parallel, cache_dir))
        self.keep_dirs.append(keep_dir)

    def network_bytes_per_s(self) -> float:
        return 42.0

    def last_network_failure(self) -> str | None:
        return "cdn.example refused the connection."


def test_the_engine_stands_in_for_the_stream_proxy(tmp_path: Path) -> None:
    module = FakeNetwork()
    reading = native_player.EngineStreamReading(tmp_path / "cache", module=module)
    assert module.settings == [(True, str(tmp_path / "cache"))]
    reading.enabled = False
    assert module.settings[-1] == (False, str(tmp_path / "cache"))
    assert reading.local_url("https://cdn.example/a.mkv") == "https://cdn.example/a.mkv"
    assert reading.upstream_bytes_per_s() == 42.0
    assert reading.last_failure() == "cdn.example refused the connection."
    (tmp_path / "cache" / "stream").mkdir(parents=True)
    # Files' opening reads are kept beside the chunk cache, and outlive it.
    assert module.keep_dirs[-1] == str(tmp_path / "cache-opening")
    (tmp_path / "cache-opening" / "file").mkdir(parents=True)
    reading.shutdown()
    assert not (tmp_path / "cache").exists()
    assert (tmp_path / "cache-opening" / "file").exists()


def test_a_sandbox_without_a_pulse_cookie_gets_libpulses_zero_cookie(tmp_path: Path) -> None:
    """The engine's PulseAudio client sends an empty cookie when it has none,
    which PipeWire refuses; libpulse sends 256 zero bytes, which it takes."""
    from gravitas.infrastructure.player.native_player import ensure_pulse_cookie

    environ: dict[str, str] = {}
    cookie = ensure_pulse_cookie(environ, home=tmp_path / "home", cache=tmp_path, platform="linux")
    assert cookie == tmp_path / "pulse-cookie"
    assert cookie.read_bytes() == bytes(256)
    assert environ["PULSE_COOKIE"] == str(cookie)


def test_a_real_pulse_cookie_is_left_to_be_used(tmp_path: Path) -> None:
    from gravitas.infrastructure.player.native_player import ensure_pulse_cookie

    home = tmp_path / "home"
    (home / ".config" / "pulse").mkdir(parents=True)
    (home / ".config" / "pulse" / "cookie").write_bytes(b"x" * 256)
    environ: dict[str, str] = {}
    assert ensure_pulse_cookie(environ, home=home, cache=tmp_path, platform="linux") is None
    assert environ == {}
    # Nor is a cookie someone named, nor anything off Linux.
    named = {"PULSE_COOKIE": "/somewhere/cookie"}
    assert ensure_pulse_cookie(named, home=tmp_path, cache=tmp_path, platform="linux") is None
    assert ensure_pulse_cookie({}, home=tmp_path, cache=tmp_path, platform="win32") is None
