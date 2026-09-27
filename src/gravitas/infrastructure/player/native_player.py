"""MediaPlayer implementation on Gravitas' own engine (native/player/).

The engine is a Rust library built into the `gravitas_player` extension
module by scripts/build_native_player.py; this adapter is all the app sees of
it. It is opt-in (GRAVITAS_PLAYER=native) while the engine works towards
parity with mpv; the roadmap is in native/player/README.md.

Tracks come back from the engine in mpv's `track-list` shape, so labels and
language questions go through the same `track_list` module as MpvPlayer's.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import sys
import threading
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from gravitas.domain import languages
from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import DecodeReport, MediaFormat, SubtitleStyle, TrackLanguages
from gravitas.infrastructure.paths import cache_dir
from gravitas.infrastructure.player import track_list
from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)

# Where scripts/build_native_player.py puts the module.
_MODULE = "gravitas.infrastructure.player.gravitas_player"

# A network read may stall this long before it counts as failed: mpv's
# numbers, for the same reasons (see _NETWORK_TIMEOUT_S in mpv_player). The
# local stream proxy gets longer, because its own rate-limit backoff can
# legitimately take that long.
_NETWORK_TIMEOUT_S = 10.0
_PROXIED_NETWORK_TIMEOUT_S = 60.0
_LOOPBACK_PREFIXES = ("http://127.0.0.1:", "http://[::1]:", "http://localhost:")

EventCallback = Callable[[str, object], None]


class SharedDevice(Protocol):
    """`gravitas_player.SharedDevice`: the Vulkan device the scene graph, the
    renderer and the decoder share."""

    @property
    def decodes(self) -> bool: ...
    def handles(self) -> dict[str, int]: ...


class NativeEngine(Protocol):
    """The part of `gravitas_player.Player` this adapter uses. A test fake
    implements the same."""

    def load(
        self,
        url: str,
        *,
        start: float = ...,
        headers: Sequence[tuple[str, str]] = ...,
        audio_languages: Sequence[str] = ...,
        subtitle_languages: Sequence[str] = ...,
        network_timeout: float | None = ...,
        keyframe_start: bool = ...,
    ) -> None: ...
    def stop(self) -> None: ...
    def set_paused(self, paused: bool) -> None: ...
    def is_paused(self) -> bool: ...
    def seek(self, seconds: float) -> None: ...
    def position(self) -> float: ...
    def duration(self) -> float | None: ...
    def set_volume(self, volume: float) -> None: ...
    def volume(self) -> float: ...
    def set_muted(self, muted: bool) -> None: ...
    def is_muted(self) -> bool: ...
    def is_loading(self) -> bool: ...
    def tracks(self) -> list[dict[str, Any]]: ...
    def select(self, kind: str, id: int | None) -> None: ...
    def selected(self, kind: str) -> int | None: ...
    def chapters(self) -> list[tuple[float, str]]: ...
    def buffered_to(self) -> float | None: ...
    def read_rate(self) -> float: ...
    def network_rate(self) -> float | None: ...
    def dropped_frames(self) -> int: ...
    def video_size(self) -> tuple[int, int] | None: ...
    def set_gpu_rendering(self, enabled: bool) -> None: ...
    def set_hardware_decoding(self, mode: str) -> None: ...
    def use_shared_device(self, device: SharedDevice) -> None: ...
    def renders_dolby_vision(self) -> bool: ...
    def set_subtitle_visible(self, visible: bool) -> None: ...
    def subtitle_visible(self) -> bool: ...
    def set_subtitle_delay(self, seconds: float) -> None: ...
    def subtitle_delay(self) -> float: ...
    def set_subtitle_style(
        self, *, font_size: float, color: int, border_size: float, back_opacity: float, bold: bool
    ) -> None: ...
    def add_subtitle(
        self, url: str, *, title: str = ..., language: str = ..., select: bool = ...
    ) -> int: ...
    def shutdown(self) -> None: ...


EngineFactory = Callable[[EventCallback], NativeEngine]


def _rgb(color: str) -> int:
    """ "#RRGGBB" (the settings' form) as 0xRRGGBB; white when unreadable."""
    try:
        return int(color.removeprefix("#")[:6], 16)
    except ValueError:
        return 0xFFFFFF


def software_rendering(environ: Mapping[str, str] = os.environ) -> bool:
    """Whether GRAVITAS_NATIVE_RENDERER asks for swscale instead of libplacebo:
    the first thing to try if a GPU driver misbehaves."""
    return environ.get("GRAVITAS_NATIVE_RENDERER", "").strip().lower() == "software"


# mpv's names for a hwdec, as FFmpeg's device types. Every native hwdec is
# copy-back here for now, so mpv's "-copy" variants mean the same thing.
_HWDEC_DEVICES = {"nvdec": "cuda"}


def hardware_decoding(environ: Mapping[str, str] = os.environ) -> str:
    """What GRAVITAS_HWDEC -- the variable the mpv engine reads -- asks of the
    native engine: "auto", "no", or one FFmpeg device type."""
    value = environ.get("GRAVITAS_HWDEC", "").strip().lower()
    if value in ("", "yes", "auto", "auto-safe", "auto-copy", "auto-unsafe"):
        return "auto"
    if value == "no":
        return "no"
    name = value.removesuffix("-copy")
    return _HWDEC_DEVICES.get(name, name)


def wants_shared_device(environ: Mapping[str, str] = os.environ) -> bool:
    """Whether decoding into the scene graph's own device is allowed: hardware
    decoding is on and not pinned to a device other than Vulkan."""
    return hardware_decoding(environ) in ("auto", "vulkan")


def create_shared_device(instance: int, get_instance_proc_addr: int) -> SharedDevice | None:
    """The engine's SharedDevice on that Vulkan instance (which must live for
    the process), or None -- logged -- when the module is not built or
    libplacebo cannot make a device there."""
    try:
        module = importlib.import_module(_MODULE)
    except ImportError:
        return None
    try:
        device: SharedDevice = module.SharedDevice(
            instance=instance, get_instance_proc_addr=get_instance_proc_addr
        )
    except RuntimeError as exc:
        _log.warning("no shared Vulkan device for the native engine: %s", exc)
        return None
    return device


class EngineStreamReading:
    """The StreamAccelerator port for the native engine, which reads streams
    itself: several connections, a chunk cache on disk (milestone 5 in
    native/player/README.md). There is nothing to stand in front of, so
    `local_url` hands every URL back and the engine does the work; `enabled`
    -- the user's accelerator setting -- turns the engine's own reading on and
    off, and the rate and the last failure are the engine's."""

    def __init__(self, cache_dir: Path, *, module: Any | None = None) -> None:
        self._cache_dir = cache_dir
        self._module = module if module is not None else importlib.import_module(_MODULE)
        self._enabled = True
        self._apply()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
        self._apply()

    @property
    def _keep_dir(self) -> Path:
        """Beside the chunk cache, and unlike it kept between sessions: files'
        opening reads (their header and index, a few MiB), so resuming one
        skips the round trips of reading them again."""
        return self._cache_dir.with_name(self._cache_dir.name + "-opening")

    def _apply(self) -> None:
        self._module.configure_network(
            parallel=self._enabled,
            cache_dir=str(self._cache_dir),
            keep_dir=str(self._keep_dir),
        )

    async def start(self) -> None:
        """Nothing to start: the engine reads on its own threads."""

    def shutdown(self) -> None:
        # A stream still playing at quit leaves its chunks; debrid bytes do
        # not outlive the session (the next start clears them too).
        shutil.rmtree(self._cache_dir, ignore_errors=True)

    def local_url(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str:
        del headers
        return url

    def holds_stream_cache(self) -> bool:
        return False

    def upstream_bytes_per_s(self) -> float:
        return float(self._module.network_bytes_per_s())

    def last_failure(self) -> str | None:
        failure: str | None = self._module.last_network_failure()
        return failure


def configure_shader_cache(directory: Path) -> None:
    """Keeps the engine's compiled shaders in `directory` between runs. Does
    nothing when the module is not built."""
    try:
        module = importlib.import_module(_MODULE)
    except ImportError:
        return
    module.configure_shader_cache(str(directory))


def requested(setting: str = "native", environ: Mapping[str, str] = os.environ) -> bool:
    """Whether the native engine is asked for: GRAVITAS_PLAYER when it names
    an engine ("native" or "mpv"), else the user's setting."""
    override = environ.get("GRAVITAS_PLAYER", "").strip().lower()
    if override in ("native", "mpv"):
        return override == "native"
    return setting == "native"


def available() -> bool:
    """Whether the engine module has been built for this checkout or bundle."""
    try:
        importlib.import_module(_MODULE)
    except ImportError:
        return False
    return True


# PulseAudio's native protocol authenticates with a cookie of this length
# (PA_NATIVE_COOKIE_LENGTH); libpulse sends that many zero bytes when it has
# none.
_PULSE_COOKIE_BYTES = 256


def ensure_pulse_cookie(
    environ: MutableMapping[str, str] = os.environ,
    home: Path | None = None,
    cache: Path | None = None,
    platform: str = sys.platform,
) -> Path | None:
    """Points PULSE_COOKIE at a zero cookie when no real one can be read, and
    returns it; None when there is nothing to do.

    The engine's PulseAudio client (the `pulseaudio` crate, through cpal)
    sends an EMPTY cookie when it finds none, where libpulse sends 256 zero
    bytes -- and PipeWire's pulse server refuses the empty one as invalid.
    Outside a sandbox ~/.config/pulse/cookie is always there, so nothing
    shows; inside the Flatpak it is not, the handshake failed, cpal fell
    back to ALSA, whose pulse plugin is the host's and not in the runtime,
    and every film played to a silent output. This hands the client what
    libpulse would have sent. Before the engine starts, while nothing of its
    reads the environment."""
    if not platform.startswith("linux") or environ.get("PULSE_COOKIE"):
        return None
    home = Path.home() if home is None else home
    if (home / ".config" / "pulse" / "cookie").exists() or (home / ".pulse-cookie").exists():
        return None
    cookie = (cache_dir() if cache is None else cache) / "pulse-cookie"
    try:
        if not cookie.is_file() or cookie.stat().st_size != _PULSE_COOKIE_BYTES:
            cookie.parent.mkdir(parents=True, exist_ok=True)
            cookie.write_bytes(bytes(_PULSE_COOKIE_BYTES))
    except OSError as exc:
        _log.info("no PulseAudio cookie to hand the engine: %s", exc)
        return None
    environ["PULSE_COOKIE"] = str(cookie)
    return cookie


def _default_factory(on_event: EventCallback, *, zero_copy: bool = False) -> NativeEngine:
    """The engine's player. With `zero_copy` (video will render on the scene
    graph's own device) it makes no GPU device of its own unless a frame
    needs one: a second device goes unused there, and making it held Qt's
    render thread -- and so the GUI -- for ~0.8 s on the click that started
    playback."""
    try:
        module = importlib.import_module(_MODULE)
    except ImportError as exc:
        raise PlaybackFailed(
            "the native player engine is not built (scripts/build_native_player.py)"
        ) from exc
    _log.info("native player engine %s on %s", module.__version__, module.ffmpeg_versions())
    ensure_pulse_cookie()
    engine: NativeEngine = module.Player(
        on_event, own_renderer="when-needed" if zero_copy else "at-once"
    )
    return engine


class NativePlayer:
    """The MediaPlayer port on the native engine.

    Engine events arrive on the engine's event thread and are passed to the
    registered callbacks there -- the same contract MpvPlayer keeps with
    mpv's thread, which PlayerController already hops from.
    """

    def __init__(
        self,
        factory: EngineFactory | None = None,
        *,
        environ: Mapping[str, str] = os.environ,
        shared_device: SharedDevice | None = None,
        zero_copy: bool = False,
    ) -> None:
        self._tracks_changed: Callable[[], None] | None = None
        self._state_changed: Callable[[], None] | None = None
        self._stream_ended: Callable[[], None] | None = None
        self._opened: Callable[[], None] | None = None
        self._load_failed: Callable[[], None] | None = None
        # Applied by the next play(), then dropped: see set_track_languages.
        self._pending_languages: TrackLanguages | None = None
        self._subtitle_language = ""
        # A reload (play() with no preference before it) reopens the same
        # file on the tracks the viewer had, which the engine learns once it
        # has opened it.
        self._restore: dict[str, int | None] | None = None
        self._restore_lock = threading.Lock()
        self._engine = (
            factory(self._on_event)
            if factory is not None
            else _default_factory(self._on_event, zero_copy=zero_copy)
        )
        if software_rendering(environ):
            _log.info("GRAVITAS_NATIVE_RENDERER=software: video renders through swscale")
            self._engine.set_gpu_rendering(False)
        hwdec = hardware_decoding(environ)
        if hwdec != "auto":
            _log.info("GRAVITAS_HWDEC: the native engine decodes with %s", hwdec)
            self._engine.set_hardware_decoding(hwdec)
        if shared_device is not None:
            # The window renders on this device (see main.py), so frames
            # decoded into it are sampled where they are.
            self._engine.use_shared_device(shared_device)

    # --- events ---

    def _on_event(self, name: str, value: object) -> None:
        """Runs on the engine's event thread."""
        callback: Callable[[], None] | None
        if name == "file-loaded":
            self._restore_tracks()
            callback = self._opened
        elif name == "load-failed":
            _log.warning("the native engine could not open the stream: %s", value)
            callback = self._load_failed
        elif name == "tracks-changed":
            callback = self._tracks_changed
        elif name == "state-changed":
            callback = self._state_changed
        elif name == "end-of-file":
            callback = self._stream_ended
        elif name == "first-frame":
            _log.info("first frame %s ms after load", value)
            callback = None
        else:
            callback = None
        if callback is not None:
            callback()

    def _restore_tracks(self) -> None:
        with self._restore_lock:
            restore, self._restore = self._restore, None
        if not restore:
            return
        for kind, track_id in restore.items():
            if self._engine.selected(kind) != track_id:
                self._engine.select(kind, track_id)

    # --- playback ---

    def play(
        self,
        url: str,
        *,
        start: float = 0.0,
        headers: Sequence[tuple[str, str]] = (),
        upstream_cached: bool = False,
    ) -> None:
        # The engine keeps no cache of its own yet, so there is nothing to
        # shrink for a proxy that caches upstream.
        del upstream_cached
        _log.info(
            "loading %s (start=%.0fs, %d custom headers) on the native engine",
            abbreviate_url(url),
            start,
            len(headers),
        )
        preference = self._pending_languages
        self._pending_languages = None
        # The engine resets the subtitle delay with every load; a reload of
        # the same file (a reconnect) keeps the correction the viewer made.
        kept_delay = self._engine.subtitle_delay() if preference is None else 0.0
        with self._restore_lock:
            if preference is None:
                self._restore = {
                    "audio": self._engine.selected("audio"),
                    "sub": self._engine.selected("sub"),
                }
            else:
                self._restore = None
                self._subtitle_language = preference.subtitle
        # A reload passes no preference: the tracks it had come back through
        # _restore_tracks. track_codes() is empty for "off" and for none.
        audio = list(languages.track_codes(preference.audio)) if preference else []
        subtitles = list(languages.track_codes(preference.subtitle)) if preference else []
        timeout = (
            _PROXIED_NETWORK_TIMEOUT_S if url.startswith(_LOOPBACK_PREFIXES) else _NETWORK_TIMEOUT_S
        )
        try:
            # Pause carries over from the last file in the engine, as it does
            # in mpv; a new load always plays, as MpvPlayer.play() makes it.
            self._engine.set_paused(False)
            if preference is not None:
                # "Off" hides rather than deselects, as MpvPlayer does, so
                # turning subtitles on mid-film costs nothing.
                self._engine.set_subtitle_visible(preference.subtitle != languages.SUBTITLES_OFF)
            self._engine.load(
                url,
                start=max(start, 0.0),
                headers=list(headers),
                audio_languages=audio,
                subtitle_languages=subtitles,
                network_timeout=timeout,
                # Every start past zero is a resume or a reload after a
                # stall, where starting at the keyframe before the point (a
                # few seconds at most; the engine caps it) costs nothing the
                # viewer minds, and decoding up to the exact point is most of
                # the wait for the picture.
                keyframe_start=start > 0,
            )
            if kept_delay:
                self._engine.set_subtitle_delay(kept_delay)
        except Exception as exc:
            raise PlaybackFailed(f"failed to play {url}: {exc}") from exc

    def stop(self) -> None:
        self._engine.stop()

    def pause(self) -> None:
        self._engine.set_paused(True)

    def resume(self) -> None:
        self._engine.set_paused(False)

    def is_paused(self) -> bool:
        return self._engine.is_paused()

    def seek(self, seconds: float) -> None:
        self._engine.seek(seconds)

    def position(self) -> float:
        return self._engine.position()

    def duration(self) -> float:
        return self._engine.duration() or 0.0

    def set_volume(self, volume: float) -> None:
        self._engine.set_volume(volume)

    def volume(self) -> float:
        return self._engine.volume()

    def set_muted(self, muted: bool) -> None:
        self._engine.set_muted(muted)

    def is_muted(self) -> bool:
        return self._engine.is_muted()

    def is_loading(self) -> bool:
        return self._engine.is_loading()

    # --- what is playing ---

    def video_dolby_vision_profile(self) -> int:
        """The selected video track's Dolby Vision profile -- but only while
        this engine cannot convert it. The port's callers use the number to
        warn about, and remember, releases that render in the wrong colours;
        through libplacebo profile 5 renders correctly, so there is nothing to
        warn about and nothing to remember."""
        if self._engine.renders_dolby_vision():
            return 0
        video = next(
            (t for t in self._engine.tracks() if t.get("type") == "video" and t.get("selected")),
            None,
        )
        profile = video.get("dolby-vision-profile") if video else None
        return int(profile) if isinstance(profile, int) else 0

    def chapters(self) -> list[tuple[float, str]]:
        return sorted(self._engine.chapters())

    def media_format(self) -> MediaFormat:
        try:
            return track_list.media_format(self._engine.tracks())
        except Exception:  # the engine was shut down under us
            return MediaFormat()

    def decode_report(self) -> DecodeReport:
        video = next(
            (t for t in self._engine.tracks() if t.get("type") == "video" and t.get("selected")),
            None,
        )
        if video is None:
            return DecodeReport()
        _, height = self.video_size()
        return DecodeReport(
            codec=str(video.get("codec") or ""),
            height=height,
            dropped_frames=self._engine.dropped_frames(),
        )

    def download_speed(self) -> float:
        # While the engine reads the stream itself, its demuxer reads a local
        # chunk cache, and only the bytes off the network describe the line
        # -- a 0.0 there is "nothing fetched lately", never a cue to measure
        # the cache instead (see CLAUDE.md on cache-speed).
        network = self._engine.network_rate()
        return network if network is not None else self._engine.read_rate()

    def buffered_to(self) -> float:
        return self._engine.buffered_to() or 0.0

    def video_size(self) -> tuple[int, int]:
        return self._engine.video_size() or (0, 0)

    # --- tracks ---

    def prepare_track_switching(self) -> None:
        # Nothing to prepare: the engine re-reads on a switch for now, and
        # will keep every track's packets itself (milestone 4).
        return

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return track_list.labelled(self._engine.tracks(), "sub")

    def audio_tracks(self) -> list[tuple[int, str]]:
        return track_list.labelled(self._engine.tracks(), "audio")

    def set_subtitle_track(self, track_id: int | None) -> None:
        # "Off" hides rather than deselects, as MpvPlayer does. Every
        # subtitle track is decoded as it is read, so switching between
        # them is instant anyway.
        if track_id is None:
            self._engine.set_subtitle_visible(False)
            return
        if self._engine.selected("sub") != track_id:
            self._engine.select("sub", track_id)
        self._engine.set_subtitle_visible(True)

    def current_subtitle_track(self) -> int | None:
        if not self._engine.subtitle_visible():
            return None
        return self._engine.selected("sub")

    def set_audio_track(self, track_id: int | None) -> None:
        self._engine.select("audio", track_id)

    def current_audio_track(self) -> int | None:
        return self._engine.selected("audio")

    def set_subtitle_delay(self, seconds: float) -> None:
        self._engine.set_subtitle_delay(seconds)

    def subtitle_delay(self) -> float:
        return self._engine.subtitle_delay()

    def choose_fallback_subtitle(self) -> bool:
        if self._engine.selected("sub") is not None:
            return False
        subs = self._sub_tracks()
        pick = track_list.fallback_subtitle(subs, self._subtitle_language)
        if pick is None:
            return False
        self._engine.select("sub", int(pick["id"]))
        self._engine.set_subtitle_visible(True)
        _log.info(
            "subtitles: no track tagged %r; chose track %s (%r) by its title",
            self._subtitle_language,
            pick["id"],
            pick.get("title"),
        )
        return True

    def has_preferred_subtitle(self) -> bool:
        return track_list.has_full_track(self._sub_tracks(), self._subtitle_language)

    def add_subtitle(self, url: str, title: str, lang: str, *, select: bool = True) -> None:
        # The engine reads the whole file now, which is what makes every
        # later switch to or from it free -- and blocks while it downloads.
        try:
            self._engine.add_subtitle(url, title=title, language=lang, select=select)
        except Exception as exc:
            raise PlaybackFailed(f"could not load subtitles {title!r}: {exc}") from exc

    def apply_subtitle_style(self, style: SubtitleStyle) -> None:
        self._engine.set_subtitle_style(
            font_size=float(style.font_size),
            color=_rgb(style.color),
            border_size=float(style.border_size),
            back_opacity=float(style.back_opacity),
            bold=style.bold,
        )

    def set_track_languages(self, languages: TrackLanguages) -> None:
        self._pending_languages = languages

    def _sub_tracks(self) -> list[dict[str, Any]]:
        return [t for t in self._engine.tracks() if t.get("type") == "sub"]

    # --- callbacks ---

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._tracks_changed = callback

    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._state_changed = callback

    def set_stream_ended_callback(self, callback: Callable[[], None] | None) -> None:
        self._stream_ended = callback

    def set_opened_callback(self, callback: Callable[[], None] | None) -> None:
        self._opened = callback

    def set_load_failed_callback(self, callback: Callable[[], None] | None) -> None:
        self._load_failed = callback

    def render_handle(self) -> object | None:
        handle: object = self._engine
        return handle

    def shutdown(self) -> None:
        self._engine.shutdown()
