"""MediaPlayer implementation on libmpv, rendered in-scene via the render API.

No `wid` embedding: foreign-window embedding is dead on Wayland. The mpv
instance is created with vo=libmpv and drawn into the Qt scene by the
presentation layer's MpvVideoItem through render_handle().
"""

from __future__ import annotations

import contextlib
import functools
import locale
import logging
import os
import sys
from collections import Counter
from collections.abc import Callable, MutableMapping, Sequence
from typing import Any

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import SubtitleStyle
from gravitas.infrastructure.graphics import hdr_mode, video_needs_system_memory
from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)

MpvFactory = Callable[[], Any]

# Where Homebrew puts libmpv.dylib (Apple Silicon, then Intel).
_MACOS_LIBMPV_DIRS = ("/opt/homebrew/lib", "/usr/local/lib")
# dyld's built-in fallback list — overridden (not extended) the moment the
# variable is set, so it must be re-included explicitly.
_MACOS_DYLD_DEFAULTS = (os.path.expanduser("~/lib"), "/usr/local/lib", "/lib", "/usr/lib")

_WINDOWS = "win32"
# The DLL names python-mpv tries, in its order (mpv.py checks %PATH% for each
# at import time). Windows has no packaging convention for libmpv at all, so
# the bundle ships libmpv-2.dll itself.
_WINDOWS_LIBMPV_NAMES = ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")

# Escape hatch: an absolute path to the libmpv library file (.dll/.so/.dylib)
# for a copy living somewhere nothing searches. The only practical way to run
# a dev checkout against a hand-placed libmpv on Windows.
_LIBMPV_OVERRIDE = "GRAVITAS_LIBMPV"


def _prepend_to_path(environ: MutableMapping[str, str], directory: str) -> None:
    """Put `directory` first on PATH, exactly once."""
    entries = [p for p in environ.get("PATH", "").split(os.pathsep) if p]
    if entries[:1] == [directory]:
        return
    environ["PATH"] = os.pathsep.join([directory, *(p for p in entries if p != directory)])


def _force_find_library(path: str) -> None:
    """Make `ctypes.util.find_library("mpv")` answer `path`.

    python-mpv is a ctypes wrapper: on Unix it asks find_library for the soname
    and CDLL()s whatever comes back. find_library consults only the system
    loader cache, so a libmpv outside it — a bundled copy, a manual build — is
    invisible until this redirect is installed. Windows never reaches here:
    there python-mpv searches %PATH% directly, with no seam to patch.
    """
    import ctypes.util

    _stock_find_library = ctypes.util.find_library

    def _find_library(name: str) -> str | None:
        if name == "mpv":
            return path
        return _stock_find_library(name)

    ctypes.util.find_library = _find_library


def _ensure_libmpv_discoverable(
    environ: MutableMapping[str, str] = os.environ, platform: str = sys.platform
) -> None:
    """Point the loader at a system libmpv the stock search would miss.

    macOS: python-mpv locates libmpv with ctypes.util.find_library, which
    searches DYLD_FALLBACK_LIBRARY_PATH — Homebrew's prefix is not in it on
    Apple Silicon, so the import fails with libmpv installed. ctypes reads the
    environment at lookup time, so extending it here (in-process) works.

    Windows: python-mpv resolves the DLL at *import* time by scanning %PATH%
    for its three known names, so a directory must be on PATH before
    `import mpv` — nothing later can help.

    Anywhere: GRAVITAS_LIBMPV names the library file outright and wins.
    """
    override = environ.get(_LIBMPV_OVERRIDE, "").strip()
    if platform == _WINDOWS:
        if override:
            _prepend_to_path(environ, os.path.dirname(override) or os.curdir)
        return
    if override:
        _force_find_library(override)
    if platform != "darwin":
        return
    existing = [p for p in environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":") if p]
    if not existing:
        existing = list(_MACOS_DYLD_DEFAULTS)
    for candidate in _MACOS_LIBMPV_DIRS:
        if candidate not in existing:
            existing.insert(0, candidate)
    environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(existing)


def _ensure_bundled_libmpv_findable(
    environ: MutableMapping[str, str] = os.environ, platform: str = sys.platform
) -> None:
    """In a PyInstaller bundle, the fat build ships libmpv next to the app so
    playback works on hosts with no system libmpv. But python-mpv resolves the
    Linux library via ctypes.util.find_library('mpv'), which only consults the
    system loader cache and never sys._MEIPASS — so on such a host the bundled
    copy is invisible and `import mpv` raises. Redirect find_library('mpv') to
    the absolute bundled soname; python-mpv then CDLL()s it directly. Its ffmpeg
    dependency closure resolves through the LD_LIBRARY_PATH PyInstaller's
    bootloader already points at _MEIPASS. No-op when not frozen (find_library
    keeps its stock behaviour, so a dev checkout uses system libmpv).

    Windows takes the other road: the search is a %PATH% scan by DLL name, so
    the bundle directory goes on PATH instead. libmpv-2.dll is statically
    linked (ffmpeg and friends are inside it), so there is no closure to place.

    GRAVITAS_LIBMPV still wins: _ensure_libmpv_discoverable has already pointed
    the loader at it, and a user who names a libmpv explicitly inside a bundle
    is debugging exactly this — the bundled copy must not quietly take it back.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is None:
        return
    if environ.get(_LIBMPV_OVERRIDE, "").strip():
        return
    import glob

    if platform == _WINDOWS:
        if any(os.path.exists(os.path.join(meipass, name)) for name in _WINDOWS_LIBMPV_NAMES):
            _prepend_to_path(environ, meipass)
        return
    # PyInstaller drops the dylib next to the app on macOS, the .so on Linux.
    pattern = "libmpv*.dylib" if platform == "darwin" else "libmpv.so*"
    matches = sorted(glob.glob(os.path.join(meipass, pattern)))
    if not matches:
        return
    _force_find_library(matches[0])


def _ensure_bundled_ytdlp_on_path(
    environ: MutableMapping[str, str] = os.environ, platform: str = sys.platform
) -> None:
    """mpv's ytdl_hook resolves YouTube/trailer streams by spawning a `yt-dlp`
    executable found on PATH (`yt-dlp.exe` on Windows, which is how mpv's own
    Windows subprocess lookup resolves the name). The fat bundle ships one next
    to the app; libmpv inherits this process's environment when it spawns, so
    prepending the bundle dir to PATH here (before mpv loads) is enough for the
    hook to find it. No-op when not frozen or when no yt-dlp was bundled — a
    system yt-dlp still works."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is None:
        return
    name = "yt-dlp.exe" if platform == _WINDOWS else "yt-dlp"
    if os.path.exists(os.path.join(meipass, name)):
        _prepend_to_path(environ, meipass)


# mpv's own message levels -> logging levels. Everything below `info`
# ("status", "v", "debug", "trace") maps to DEBUG: it only surfaces on a
# GRAVITAS_LOG_LEVEL=DEBUG run anyway.
_MPV_LOG_LEVELS = {
    "fatal": logging.CRITICAL,
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "info": logging.INFO,
}


def _mpv_log_handler(level: str, prefix: str, text: str) -> None:
    """Route libmpv's terminal output (demuxer/ffmpeg/network errors, VO/AO
    setup lines) into our logging tree as `mpv.<component>`. Fires on mpv's
    own thread; the logging module is thread-safe, and no Qt object is
    touched here."""
    logging.getLogger(f"mpv.{prefix}").log(
        _MPV_LOG_LEVELS.get(level, logging.DEBUG), "%s", text.rstrip()
    )


def _hdr_options(mode: str | None) -> dict[str, object]:
    """mpv options that stop it flattening HDR on its way to our surface.

    Left to itself mpv tone-maps to SDR and dithers to 8 bits, because it
    assumes it is drawing to an ordinary display. Neither is true here: the
    surface it renders into is 10-bit or float, and Qt hands it to a swapchain
    KWin drives in HDR. Both assumptions have to be corrected together --
    correcting one alone gives either a dark, wrongly-mapped picture or a
    correctly-mapped one that bands.

    `target-peak` is deliberately left alone: mpv reads the display's real
    capability, which beats any number hardcoded here. `hdr-compute-peak` stays
    off because it measures each frame on the GPU and makes brightness drift
    shot to shot -- passthrough should be passthrough.
    """
    if mode is None:
        return {}
    if mode == "scrgb":
        # Linear light with sRGB primaries, values above 1.0 carrying anything
        # brighter than SDR white. That is scRGB, and it is why this mode needs
        # the float surface.
        return {"target_prim": "bt.709", "target_trc": "linear", "dither_depth": "no"}
    # HDR10: Rec. 2020 primaries, PQ curve, 10 bits -- the encoding the
    # swapchain itself is asking for, handed over already in that form.
    return {"target_prim": "bt.2020", "target_trc": "pq", "dither_depth": 10}


def _default_factory(*, zero_copy_video: bool = False) -> Any:
    # libmpv needs the C numeric locale; Qt may have changed it. Must run
    # right before mpv.MPV() construction (after QGuiApplication init),
    # not at import time.
    locale.setlocale(locale.LC_NUMERIC, "C")

    _ensure_libmpv_discoverable()
    _ensure_bundled_libmpv_findable()
    _ensure_bundled_ytdlp_on_path()

    import mpv  # type: ignore[import-untyped]

    # `auto-safe` is the right default: it only enables a hwdec known not to
    # glitch. But it is conservative -- on an NVIDIA card whose CUDA/NVDEC path
    # is unavailable (no `nvidia-uvm`, driver-only-GLX setups) it declines the
    # GPU entirely and mpv decodes in software, pinning every core. GRAVITAS_HWDEC
    # lets such a box force a working backend that auto-safe skips -- e.g.
    # `vdpau` (NVIDIA's decode API, works from GLX with no CUDA) or `vaapi` --
    # without a rebuild. Any mpv hwdec value is accepted; empty falls back.
    #
    # Windows and macOS are the exceptions, for reasons that meet in the same
    # place: the frames have to be in system memory. Windows decodes to
    # D3D11/DXVA2 surfaces the OpenGL VO cannot import; macOS renders video
    # through libmpv's software render API (its scene graph is on Metal), which
    # reads frames from the CPU. Either way the zero-copy backends auto-safe
    # would pick are unusable, and the fallback would be software decoding of
    # 4K HEVC on the CPU. `auto-copy` picks the best backend that copies frames
    # back instead: a readback per frame, still an order of magnitude cheaper
    # than decoding in software. mpv falls back to software on its own if none
    # initialises. See infrastructure/graphics.py.
    #
    # zero_copy_video is the macOS escape from that: when the native video
    # bridge is in use, mpv renders through OpenGL into a surface Metal reads
    # directly, so a GPU frame has somewhere to go after all and the readback
    # is pure waste.
    copy_back = video_needs_system_memory() and not zero_copy_video
    default_hwdec = "auto-copy" if copy_back else "auto-safe"
    hwdec = os.environ.get("GRAVITAS_HWDEC", "").strip() or default_hwdec

    return mpv.MPV(
        vo="libmpv",
        hwdec=hwdec,
        **_hdr_options(hdr_mode()),
        osc=False,
        input_default_bindings=False,
        keep_open="yes",  # hold the last frame instead of tearing the surface down
        # `info` excludes mpv's per-frame status line but keeps the useful
        # one-shot lines: chosen VO/AO, video format, and — crucial for
        # streaming — demuxer/ffmpeg HTTP errors with their real cause.
        log_handler=_mpv_log_handler,
        # mpv's verbosity follows ours. At GRAVITAS_LOG_LEVEL=DEBUG it gets
        # `v`, which is where it prints the VO it chose, the GL vendor and
        # renderer strings, the hwdec it settled on and the demuxer's view of
        # the stream -- exactly the lines a "black video" or "this stream will
        # not open" report needs, and noise otherwise.
        loglevel="v" if logging.getLogger().isEnabledFor(logging.DEBUG) else "info",
    )


# ISO 639-1 and common 639-2 codes -> display names. Containers carry either.
_LANG_NAMES = {
    "en": "English",
    "eng": "English",
    "de": "German",
    "ger": "German",
    "deu": "German",
    "fr": "French",
    "fre": "French",
    "fra": "French",
    "es": "Spanish",
    "spa": "Spanish",
    "it": "Italian",
    "ita": "Italian",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "ja": "Japanese",
    "jpn": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "chi": "Chinese",
    "zho": "Chinese",
    "ar": "Arabic",
    "ara": "Arabic",
    "hi": "Hindi",
    "hin": "Hindi",
    "tr": "Turkish",
    "tur": "Turkish",
    "pl": "Polish",
    "pol": "Polish",
    "nl": "Dutch",
    "dut": "Dutch",
    "nld": "Dutch",
    "sv": "Swedish",
    "swe": "Swedish",
    "no": "Norwegian",
    "nor": "Norwegian",
    "da": "Danish",
    "dan": "Danish",
    "fi": "Finnish",
    "fin": "Finnish",
    "cs": "Czech",
    "cze": "Czech",
    "ces": "Czech",
    "el": "Greek",
    "gre": "Greek",
    "ell": "Greek",
    "he": "Hebrew",
    "heb": "Hebrew",
    "hu": "Hungarian",
    "hun": "Hungarian",
    "ro": "Romanian",
    "rum": "Romanian",
    "ron": "Romanian",
    "uk": "Ukrainian",
    "ukr": "Ukrainian",
    "th": "Thai",
    "tha": "Thai",
    "vi": "Vietnamese",
    "vie": "Vietnamese",
    "id": "Indonesian",
    "ind": "Indonesian",
    "fa": "Persian",
    "per": "Persian",
    "fas": "Persian",
    "bg": "Bulgarian",
    "bul": "Bulgarian",
    "hr": "Croatian",
    "hrv": "Croatian",
    "sr": "Serbian",
    "srp": "Serbian",
    "sk": "Slovak",
    "slo": "Slovak",
    "slk": "Slovak",
    "sl": "Slovenian",
    "slv": "Slovenian",
    "lt": "Lithuanian",
    "lit": "Lithuanian",
    "lv": "Latvian",
    "lav": "Latvian",
    "et": "Estonian",
    "est": "Estonian",
    "ca": "Catalan",
    "cat": "Catalan",
    "ms": "Malay",
    "may": "Malay",
    "msa": "Malay",
    "ta": "Tamil",
    "tam": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
}

_CHANNEL_LABELS = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}


def _lang_name(code: object) -> str:
    """'en' -> 'English', 'fr-CA' -> 'French (CA)'; unknown codes pass through."""
    if not isinstance(code, str) or not code:
        return ""
    base, _, region = code.partition("-")
    name = _LANG_NAMES.get(base.lower())
    if name is None:
        return code
    return f"{name} ({region.upper()})" if region else name


def _track_label(track: dict[str, Any]) -> str:
    lang = _lang_name(track.get("lang"))
    title = str(track.get("title") or "")
    if title and lang and lang.split(" (")[0].lower() in title.lower():
        # The title already names the language ("English (United States)") —
        # prefixing the code again is noise.
        parts = [title]
    elif title and lang:
        parts = [lang, title]
    elif title or lang:
        parts = [title or lang]
    else:
        parts = [f"Track {track['id']}"]
    if track.get("type") == "audio":
        count = track.get("demux-channel-count")
        if isinstance(count, int) and count:
            channels = _CHANNEL_LABELS.get(count, f"{count}ch")
            if channels.lower() not in " ".join(parts).lower():
                parts.append(channels)
    label = " · ".join(parts)
    flags = [
        name
        for key, name in (
            ("forced", "Forced"),
            ("hearing-impaired", "SDH"),
            ("visual-impaired", "AD"),
        )
        if track.get(key)
    ]
    if flags:
        label += " — " + ", ".join(flags)
    return label


class MpvPlayer:
    def __init__(self, factory: MpvFactory | None = None, *, zero_copy_video: bool = False) -> None:
        if factory is None:
            factory = functools.partial(_default_factory, zero_copy_video=zero_copy_video)
        self._tracks_changed_callback: Callable[[], None] | None = None
        self._state_changed_callback: Callable[[], None] | None = None
        self._track_observer_registered = False
        self._state_observers_registered = False
        try:
            self._mpv = factory()
        except Exception as exc:  # surface any libmpv init failure uniformly
            raise PlaybackFailed(f"failed to initialise libmpv: {exc}") from exc
        self._log_decode_path()

    def _log_decode_path(self) -> None:
        """Report which decoder mpv actually settled on, once per stream.

        `hwdec=auto-safe` requesting hardware decoding does not mean it engaged:
        if the ffmpeg behind libmpv lacks the codec's hwaccel (e.g. no nvdec
        build, or the GPU driver's decode libs are missing) mpv silently falls
        back to the software `vd-lavc` path, which pins every core on 1080p/4K.
        `hwdec-current` is the ground truth -- "no" means software decode.
        This is the single fastest way to tell a config problem from a missing
        system dependency, so it is logged at INFO on every file, not hidden
        behind a debug flag.
        """

        def _on_hwdec(_name: str, value: object) -> None:
            if not value:  # None before load, "" / "no" once mpv gives up on hw
                return
            codec = self._prop("video-codec") or "?"
            if value == "no":
                _log.warning(
                    "video decoding in SOFTWARE (%s); hwdec did not engage -- "
                    "check that libmpv's ffmpeg has this codec's hwaccel",
                    codec,
                )
            else:
                _log.info("video decoding via hwdec=%s (%s)", value, codec)

        with contextlib.suppress(Exception):
            self._mpv.observe_property("hwdec-current", _on_hwdec)

    # --- playback ---

    def play(
        self,
        url: str,
        *,
        start: float = 0.0,
        headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        _log.info(
            "loading %s (start=%.0fs, %d custom headers)",
            abbreviate_url(url),
            start,
            len(headers),
        )
        try:
            # `start` is applied by mpv when the file loads, so resuming needs
            # no seek-after-file-loaded race. Always assign it: mpv keeps the
            # option across loads, and a stale value would seek the next file.
            self._mpv.start = start if start > 0 else 0
            # Same reasoning: http-header-fields persists across loads, so it
            # must be assigned every time or the previous stream's Referer
            # leaks onto the next one. mpv wants "Key: value" strings.
            self._mpv.http_header_fields = [f"{key}: {value}" for key, value in headers]
            self._mpv.play(url)
            self._mpv.pause = False
        except Exception as exc:
            raise PlaybackFailed(f"failed to play {url}: {exc}") from exc

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._mpv.command("stop")

    def pause(self) -> None:
        self._mpv.pause = True

    def resume(self) -> None:
        self._mpv.pause = False

    def is_paused(self) -> bool:
        return bool(self._prop("pause"))

    def seek(self, seconds: float) -> None:
        # Absolute seeks fail harmlessly before the file is loaded.
        with contextlib.suppress(Exception):
            self._mpv.seek(seconds, reference="absolute")

    def position(self) -> float:
        value = self._prop("time_pos")
        return float(value) if value is not None else 0.0

    def duration(self) -> float:
        value = self._prop("duration")
        return float(value) if value is not None else 0.0

    # --- audio ---

    def set_volume(self, volume: float) -> None:
        self._mpv.volume = max(0.0, min(100.0, volume))

    def volume(self) -> float:
        value = self._prop("volume")
        return float(value) if value is not None else 100.0

    def set_muted(self, muted: bool) -> None:
        self._mpv.mute = muted

    def is_muted(self) -> bool:
        return bool(self._prop("mute"))

    def is_loading(self) -> bool:
        return bool(self._prop("paused_for_cache")) or bool(self._prop("seeking"))

    def buffered_to(self) -> float:
        # demuxer-cache-time: the absolute timestamp the demuxer has data up
        # to. Unavailable (None) before the file opens and for local files.
        value = self._prop("demuxer_cache_time")
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    # --- tracks ---

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return self._tracks("sub")

    def set_subtitle_track(self, track_id: int | None) -> None:
        self._mpv.sid = "no" if track_id is None else track_id

    def audio_tracks(self) -> list[tuple[int, str]]:
        return self._tracks("audio")

    def set_audio_track(self, track_id: int | None) -> None:
        self._mpv.aid = "no" if track_id is None else track_id

    def current_subtitle_track(self) -> int | None:
        return self._current_track("sid")

    def current_audio_track(self) -> int | None:
        return self._current_track("aid")

    def apply_subtitle_style(self, style: SubtitleStyle) -> None:
        self._mpv.sub_font_size = style.font_size
        self._mpv.sub_color = style.color
        self._mpv.sub_border_size = style.border_size
        # mpv colors are #AARRGGBB; opacity % -> alpha byte on black.
        alpha = round(style.back_opacity * 255 / 100)
        self._mpv.sub_back_color = f"#{alpha:02X}000000"
        self._mpv.sub_bold = style.bold

    def _current_track(self, prop: str) -> int | None:
        # mpv returns an int id, or False/"no"/None when disabled.
        value = self._prop(prop)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _tracks(self, kind: str) -> list[tuple[int, str]]:
        tracks: list[tuple[int, str]] = []
        for track in self._mpv.track_list:
            if track.get("type") == kind:
                tracks.append((int(track["id"]), _track_label(track)))
        # Identically-labelled tracks (same language, no distinguishing
        # metadata) get an index so the menu rows aren't interchangeable.
        counts = Counter(label for _, label in tracks)
        seen: Counter[str] = Counter()
        deduped: list[tuple[int, str]] = []
        for tid, label in tracks:
            if counts[label] > 1:
                seen[label] += 1
                deduped.append((tid, f"{label} · #{seen[label]}"))
            else:
                deduped.append((tid, label))
        return deduped

    # --- callbacks / renderer ---

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._tracks_changed_callback = callback
        if callback is None:
            # Leave any existing observer registered -- unobserve_property is
            # fragile across python-mpv/libmpv versions, and _on_track_list
            # is a no-op once the stored callback is cleared, so this is
            # harmless.
            return
        if self._track_observer_registered:
            return
        # python-mpv is untyped and libmpv may not be available in all
        # environments; a failure to register the observer must not
        # crash the player — the callback simply won't fire.
        with contextlib.suppress(Exception):
            self._mpv.observe_property("track-list", self._on_track_list)
            self._track_observer_registered = True

    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._state_changed_callback = callback
        if callback is None or self._state_observers_registered:
            return
        with contextlib.suppress(Exception):
            self._mpv.observe_property("pause", self._on_state)
            self._mpv.observe_property("duration", self._on_state)
            self._mpv.observe_property("mute", self._on_state)
            self._state_observers_registered = True

    def render_handle(self) -> object | None:
        handle: object = self._mpv
        return handle

    def _on_track_list(self, _name: str, _value: object) -> None:
        if self._tracks_changed_callback is not None:
            self._tracks_changed_callback()

    def _on_state(self, _name: str, _value: object) -> None:
        if self._state_changed_callback is not None:
            self._state_changed_callback()

    def _prop(self, name: str) -> Any:
        # Attribute access reads mpv PROPERTIES; dict-style access on the MPV
        # object reads the options/ namespace, where runtime state like
        # time_pos does not exist.
        try:
            return getattr(self._mpv, name)
        except Exception:
            return None

    def shutdown(self) -> None:
        self._mpv.terminate()
