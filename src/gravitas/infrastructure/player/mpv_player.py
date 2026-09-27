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
import threading
import time
import weakref
from collections.abc import Callable, MutableMapping, Sequence
from typing import Any

from gravitas.domain import languages
from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import DecodeReport, MediaFormat, SubtitleStyle, TrackLanguages
from gravitas.infrastructure.graphics import hdr_mode, video_needs_system_memory
from gravitas.infrastructure.paths import cache_dir
from gravitas.infrastructure.player import track_list
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


# ffmpeg's "this file uses something I have not implemented, send us a
# sample" boilerplate (avpriv_report_missing_feature / avpriv_request_sample).
# It is addressed to ffmpeg's developers, says nothing about whether the file
# plays -- an h264 stream with late SEI plays fine and repeats the pair every
# couple of seconds for the whole episode -- and at WARNING it buried the lines
# that do matter. Still logged, at DEBUG.
_FFMPEG_DEVELOPER_NOTES = (
    "Update your FFmpeg version to the newest one from Git",
    "If you want to help, upload a sample of this file",
)


# mpv reporting that its demuxer queues hit their limit. With the hot-audio
# graph installed this is the graph's doing -- every audio track is selected,
# so a track with a gap in it (a commentary, a partial dub) holds everything
# else in the queues while mpv reads ahead looking for its next packet -- and
# it is what both stalls in a real session began with (South Park S6E1: six
# audio tracks, "audio/6: 0 packets", then an audio underrun and a frozen
# picture). MpvPlayer listens for it here, since the handler is built before
# any player exists.
_QUEUE_OVERFLOW = "Too many packets in the demuxer packet queues"
# Weak: a player that is gone must neither be kept alive by this list nor hear
# another player's overflow.
_queue_overflow_listeners: list[weakref.WeakMethod[Callable[[], None]]] = []


def _mpv_log_handler(level: str, prefix: str, text: str) -> None:
    """Route libmpv's terminal output (demuxer/ffmpeg/network errors, VO/AO
    setup lines) into our logging tree as `mpv.<component>`. Fires on mpv's
    own thread; the logging module is thread-safe, and no Qt object is
    touched here."""
    if _QUEUE_OVERFLOW in text:
        for ref in list(_queue_overflow_listeners):
            listener = ref()
            if listener is None:
                with contextlib.suppress(ValueError):
                    _queue_overflow_listeners.remove(ref)
            else:
                listener()
    severity = _MPV_LOG_LEVELS.get(level, logging.DEBUG)
    if severity == logging.WARNING and any(note in text for note in _FFMPEG_DEVELOPER_NOTES):
        severity = logging.DEBUG
    logging.getLogger(f"mpv.{prefix}").log(severity, "%s", text.rstrip())


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


# Options handed to libavformat's HTTP protocol, which is what actually reads
# the stream off the wire.
#
# ffmpeg gives up on a read that fails while the connection is down: it reports
# a truncated read as *end of file*, so mpv concludes the file finished and --
# with keep-open -- pauses on the last frame. Nothing in that sequence says
# "stalled", so paused-for-cache never goes true, no spinner appears, and
# nothing retries when the network comes back.
#
# `reconnect_on_network_error` is the one that matters here: plain `reconnect`
# (which mpv already sets) only re-issues the request when the *server* closed
# the connection cleanly; a socket that failed because there was no network
# aborts instead.
#
# `reconnect_streamed` is NOT here, and must not come back. It reads like the
# same idea for a source that serves no byte ranges, but it stops every
# ytdl-resolved stream from opening: yt-dlp hands mpv an EDL naming byte
# ranges into googlevideo, and a reconnect that reissues the request from zero
# contradicts the range the EDL asked for. ffmpeg reports that as "End of
# file" and retries on its own backoff forever, so a trailer shows a spinner
# and never plays. Measured on one trailer, 3/3 failing with the option and
# 3/3 playing without it. What it was here to cover -- a source that cannot be
# resumed by seeking -- is covered anyway by PlayerController's stall
# recovery, which reloads the URL at the position playback froze at.
#
# No `reconnect_on_http_error`: its value is a comma-separated list, and mpv
# splits stream-lavf-o on commas.
# `reconnect_delay_max` is also how long ffmpeg keeps a failure to itself.
# It retries at 0, 1, 3, 7... seconds and stops once the next delay would
# exceed this, so the old 30 meant ~26 seconds of silent retrying before
# anything above heard about a source that was never going to open. 5 cuts
# that to ~4 seconds. Nothing is lost by it: PlayerController's own recovery
# is the patient layer, it backs off to 30s over 20 attempts, and it is the
# only one that can tell "the host refused" from "the wifi blinked" and pick
# a different source.
_STREAM_LAVF_OPTIONS = "reconnect=1,reconnect_on_network_error=1,reconnect_delay_max=5"

# MPV_END_FILE_REASON_ERROR. Spelled out rather than imported: libmpv is
# lazy-imported inside the factory (see the module docstring), so a constant
# read at import time would undo that, and this number is part of the C ABI.
_END_FILE_ERROR = 4


def _end_file_reason(event: object) -> int | None:
    """The reason out of an END_FILE event, whatever shape this python-mpv
    hands it over in.

    Recent versions pass an `MpvEvent` whose `.data` is the C struct; older
    ones pass a plain dict. Neither is worth depending on for something whose
    failure mode is a player that stops reporting dead sources, so both are
    read and anything else answers None, which is treated as "not an error".
    """
    data = getattr(event, "data", event)
    reason = getattr(data, "reason", None)
    if reason is None and isinstance(data, dict):
        reason = data.get("reason")
    if isinstance(reason, int):
        return reason
    # Some builds report it as the enum member or its name.
    value = getattr(reason, "value", None)
    return value if isinstance(value, int) else None


# How deep mpv reads ahead, and -- the part that decides whether stepping back
# feels like a player or like a download -- how much of what it already read it
# keeps.
#
# mpv's own defaults are ten seconds forward (`cache-secs`) and 50 MiB back,
# which at the bitrates this app recommends is a buffer of about 30 MB: any dip
# below the file's bitrate lasting longer than that stops playback, and seeking
# back half a minute refetches.
#
# The numbers below come in two sizes because the cache can live in two places.
# On disk (`cache-on-disk`) it is bounded by free space rather than by RAM, so
# it is set to minutes in both directions: seeking anywhere inside what has
# already been watched is then instant and costs no network at all, which is
# the whole of what "responsive" means here. mpv writes the payload to
# `cache-dir` and deletes it when playback ends. Falling back to memory, the
# same settings would be a gigabyte of RSS, so they are cut to something a
# desktop app may reasonably hold.
_CACHE_SECS = 3600
_DISK_MAX_BYTES = 2 * 1024 * 1024 * 1024
_DISK_MAX_BACK_BYTES = 1024 * 1024 * 1024
_MEMORY_MAX_BYTES = 384 * 1024 * 1024
_MEMORY_MAX_BACK_BYTES = 192 * 1024 * 1024
# After an underrun, wait until this many seconds are buffered before playing
# on. mpv's default is one second, which resumes into the same empty cache and
# stutters again a moment later; a single longer wait reads as one pause
# instead of a minute of hiccups.
_CACHE_PAUSE_WAIT = 4
# The same wait before the FIRST frame (`cache-pause-initial`), which mpv
# governs with the same option. Four seconds there is four seconds of video
# downloaded before anything shows -- ~12 MB of a 25 Mbps file -- to guard
# against a stall that a fresh connection is least likely to have. play()
# starts each file at this, and the first frame raises it to the above.
_CACHE_PAUSE_WAIT_START = 1
# How long mpv waits on a connection that has gone silent. mpv's default is
# 60s, and ffmpeg's reconnect (see _STREAM_LAVF_OPTIONS) retries a timed-out
# connect several times over: a CDN node that accepts the connection and
# never answers held the spinner for minutes before the source fallback got
# its turn. A refused connection fails at once and is not affected.
_NETWORK_TIMEOUT_S = 10
# The same timeout when mpv is reading the local stream proxy. The proxy
# already handles an upstream that goes quiet -- its own timeouts, a 502 for
# a host it cannot reach, backoff on 429 -- and its backoff waits can
# legitimately run past ten seconds. Cut off at ten, mpv abandoned the proxy
# mid-backoff and reconnected, which only sent more requests at a host
# asking for fewer (measured: a TorBox 429 storm became a stall with
# "Will reconnect at 6291456 ... error=Connection timed out"). mpv's default.
_PROXIED_NETWORK_TIMEOUT_S = 60
_LOOPBACK_PREFIXES = ("http://127.0.0.1:", "http://[::1]:", "http://localhost:")
# mpv's own cache while it reads a stream proxy that keeps the stream on disk
# itself (play(upstream_cached=True)). The proxy then holds gigabytes around
# the playhead and serves any of it at loopback speed, so a second copy in
# mpv's cache buys nothing -- except that mpv's copy only holds packets of the
# *selected* streams, so it cannot answer the refresh seek a subtitle switch
# makes anyway. Kept in memory and small: about a minute at 25 Mbps, enough
# to ride out a hiccup on the loopback socket, which is the only thing
# between mpv and the proxy's disk.
_UPSTREAM_CACHED_OPTIONS: tuple[tuple[str, Any], ...] = (
    ("cache-on-disk", "no"),
    ("demuxer-max-bytes", 192 * 1024 * 1024),
    ("demuxer-max-back-bytes", 96 * 1024 * 1024),
)
# One read from the network. mpv's default is 128 KiB, which is a lot of
# syscalls at 25 Mbps for no reason.
_STREAM_BUFFER_SIZE = "4MiB"


# How many audio tracks may be held decoded at once (see _audio_graph).
# Every track in the graph is demuxed AND decoded for as long as the file
# plays, which costs nothing in bandwidth (they all come out of the same byte
# stream) but is real CPU. A remux with a dozen lossless tracks is where that
# stops being free, so past this count the graph is not installed at all and
# audio switching falls back to mpv's own `aid`, slow but cheap to run.
_MAX_HOT_AUDIO_TRACKS = 8


def _audio_graph(selected: int, track_ids: Sequence[int]) -> str:
    """A --lavfi-complex graph that plays `selected` and keeps the rest hot.

    Setting `aid` is not a cheap operation on a network stream: mpv drops the
    packets of every stream it is not playing, so selecting another one sends
    the demuxer back to the current position to re-read it. That is a fresh
    HTTP request, the whole read-ahead thrown away, and a re-buffer before
    playback resumes -- 2.3s measured against a host serving at 5x the file's
    bitrate, 9.6s at 1.4x, both with everything already on disk in mpv's cache.

    A lavfi-complex graph selects every track it names, so routing the chosen
    track to [ao] and sinking the others keeps all of their packets in the
    cache. Switching is then a graph swap over data mpv already holds: no
    request, no re-buffer, measured at zero frames of stall (44.1 kHz tone
    checked out of the AO to confirm the switch really happened).

    The graph must be installed while the file is young -- it selects new
    streams, so installing it mid-playback pays exactly the re-read it exists
    to avoid.
    """
    parts = [f"[aid{selected}] anull [ao]"]
    parts += [f"[aid{track_id}] anullsink" for track_id in track_ids if track_id != selected]
    return " ; ".join(parts)


# What the cache directory option is called. mpv renamed it: 0.41 has
# `demuxer-cache-dir`, older builds `cache-dir`. Both are tried, and neither
# being present is survivable -- see _set_option.
_CACHE_DIR_OPTIONS = ("demuxer-cache-dir", "cache-dir")


def _disk_cache_dir() -> str | None:
    """Where mpv may spill its demuxer cache, or None to keep it in memory.

    Never fatal: a read-only or full cache directory means the cache lives in
    RAM at the smaller size, which is what it did before this existed.
    """
    try:
        path = cache_dir() / "mpv"
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.info("no on-disk demuxer cache (%s); keeping it in memory", exc)
        return None
    return str(path)


def _set_option(handle: Any, name: str, value: Any) -> bool:
    """Set one mpv option, reporting whether this build has it.

    Every option below is tuning, and no tuning is worth a player that will
    not start. Passing them to `mpv.MPV(...)` made that impossible to honour:
    the constructor applies them in one go, so a single name this libmpv does
    not know -- `cache-dir`, renamed to `demuxer-cache-dir` in 0.41 -- took
    down playback entirely with `'mpv option does not exist'`. Set one at a
    time, after construction, and an unknown one costs exactly itself.
    """
    try:
        handle[name] = value
    except Exception as exc:
        _log.debug("mpv option %s not applied (%s)", name, exc)
        return False
    return True


def _read_option(handle: Any, name: str) -> Any:
    """An option's current value, or None where this build does not have it."""
    try:
        return handle[name]
    except Exception:
        return None


def _apply_cache_options(handle: Any, *, directory: str | None = None) -> bool:
    """Read-ahead and back-buffer settings. Returns whether the cache ended up
    on disk.

    Sizing follows placement, and that is the whole point of doing this here.
    mpv's default back-buffer is 50 MiB, so stepping back a minute refetches;
    in memory it cannot be raised far without a gigabyte of RSS. On disk it is
    bounded by free space instead, so it holds minutes in **both** directions
    and anywhere already watched is instant and costs no network.
    """
    if directory is None:
        directory = _disk_cache_dir()
    _set_option(handle, "cache", "yes")
    _set_option(handle, "cache-secs", _CACHE_SECS)
    _set_option(handle, "cache-pause-wait", _CACHE_PAUSE_WAIT)
    _set_option(handle, "cache-pause-initial", "yes")
    _set_option(handle, "network-timeout", _NETWORK_TIMEOUT_S)
    _set_option(handle, "stream-buffer-size", _STREAM_BUFFER_SIZE)

    on_disk = directory is not None and _set_option(handle, "cache-on-disk", "yes")
    if on_disk and directory is not None:
        # Not fatal if neither name lands: mpv then spills to its own default
        # cache directory, which is still disk.
        for name in _CACHE_DIR_OPTIONS:
            if _set_option(handle, name, directory):
                break
    # Only after cache-on-disk actually took: a gigabyte of back-buffer is
    # reasonable on a filesystem and not in RAM.
    _set_option(handle, "demuxer-max-bytes", _DISK_MAX_BYTES if on_disk else _MEMORY_MAX_BYTES)
    _set_option(
        handle,
        "demuxer-max-back-bytes",
        _DISK_MAX_BACK_BYTES if on_disk else _MEMORY_MAX_BACK_BYTES,
    )
    _log.info(
        "demuxer cache %s (%s read-ahead, %s kept behind)",
        f"on disk at {directory}" if on_disk else "in memory",
        _human_bytes(_DISK_MAX_BYTES if on_disk else _MEMORY_MAX_BYTES),
        _human_bytes(_DISK_MAX_BACK_BYTES if on_disk else _MEMORY_MAX_BACK_BYTES),
    )
    return on_disk


def _human_bytes(count: int) -> str:
    return f"{count / (1024 * 1024):.0f} MiB"


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

    handle = mpv.MPV(
        vo="libmpv",
        hwdec=hwdec,
        **_hdr_options(hdr_mode()),
        osc=False,
        input_default_bindings=False,
        keep_open="yes",  # hold the last frame instead of tearing the surface down
        stream_lavf_o=_STREAM_LAVF_OPTIONS,
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
    # After construction, one at a time: read far ahead and keep what was
    # read, so a slow stretch is absorbed rather than watched and a seek
    # backwards is not a download. None of it is worth failing over.
    _apply_cache_options(handle)
    return handle


class MpvPlayer:
    def __init__(self, factory: MpvFactory | None = None, *, zero_copy_video: bool = False) -> None:
        if factory is None:
            factory = functools.partial(_default_factory, zero_copy_video=zero_copy_video)
        self._tracks_changed_callback: Callable[[], None] | None = None
        self._state_changed_callback: Callable[[], None] | None = None
        self._stream_ended_callback: Callable[[], None] | None = None
        self._load_failed_callback: Callable[[], None] | None = None
        self._opened_callback: Callable[[], None] | None = None
        self._track_observer_registered = False
        self._state_observers_registered = False
        self._eof_observer_registered = False
        self._end_file_observer_registered = False
        # The audio tracks currently named by the lavfi-complex graph, and
        # which of them is routed to the output. Empty means no graph: mpv's
        # own `aid` is doing the selecting. Both reset per file in play().
        self._hot_audio: tuple[int, ...] = ()
        self._audio_selection: int | None = None
        # Set when the graph had to be taken down for this file (see
        # _on_queue_overflow); it is not put back until the next file.
        self._hot_audio_refused = False
        self._graph_lock = threading.Lock()
        _queue_overflow_listeners.append(weakref.WeakMethod(self._on_queue_overflow))
        # Applied by the next play(), then dropped: see set_track_languages.
        self._pending_languages: TrackLanguages | None = None
        # The preferred subtitle language of the file playing, for
        # choose_fallback_subtitle.
        self._subtitle_language = ""
        # Start-up timing, per file: when play() was called, when mpv had the
        # file open, and whether the first frame is still to come.
        self._play_started: float | None = None
        self._opened_after: float | None = None
        try:
            self._mpv = factory()
        except Exception as exc:  # surface any libmpv init failure uniformly
            raise PlaybackFailed(f"failed to initialise libmpv: {exc}") from exc
        # The cache settings the factory chose, read back so a direct load can
        # restore them after a proxied one shrank them (see play()). Only the
        # options this build reports are remembered.
        self._cache_defaults: tuple[tuple[str, Any], ...] = tuple(
            (name, value)
            for name, _ in _UPSTREAM_CACHED_OPTIONS
            if (value := _read_option(self._mpv, name)) is not None
        )
        self._log_decode_path()
        self._watch_start_up()

    def _watch_start_up(self) -> None:
        """Time each file from play() to its first frame, and end the start-up
        buffering policy once it is on screen.

        Nothing measured this before, so "the player is slow to start" had no
        breakdown. `file-loaded` is mpv having the file open (the redirect,
        the connection, the header and index reads); the first
        `playback-restart` after it is the first frame decoded and shown,
        after the initial buffering and any resume seek. Both arrive on mpv's
        thread, which is fine: they only log and set an option."""
        with contextlib.suppress(Exception):
            self._mpv.event_callback("file-loaded")(self._on_file_loaded)
            self._mpv.event_callback("playback-restart")(self._on_playback_restart)

    def _on_file_loaded(self, _event: object) -> None:
        if self._play_started is not None and self._opened_after is None:
            self._opened_after = time.monotonic() - self._play_started
        if self._opened_callback is not None:
            self._opened_callback()

    def set_opened_callback(self, callback: Callable[[], None] | None) -> None:
        self._opened_callback = callback

    def _on_playback_restart(self, _event: object) -> None:
        started = self._play_started
        if started is None:
            return  # a seek within a file already playing
        self._play_started = None
        total = time.monotonic() - started
        opened = self._opened_after
        # Past the first frame, an underrun is a mid-film stall: from here on
        # the longer wait is the right one.
        _set_option(self._mpv, "cache-pause-wait", _CACHE_PAUSE_WAIT)
        _log.info(
            "first frame %.0f ms after load (%s)",
            total * 1000,
            f"file open at {opened * 1000:.0f} ms, then {(total - opened) * 1000:.0f} ms "
            "buffering and decoding"
            if opened is not None
            else "open time not reported",
        )

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
        upstream_cached: bool = False,
    ) -> None:
        _log.info(
            "loading %s (start=%.0fs, %d custom headers%s)",
            abbreviate_url(url),
            start,
            len(headers),
            ", cached upstream" if upstream_cached else "",
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
            # A graph naming the previous file's track ids would fail to build
            # against the new one; prepare_track_switching installs a fresh one
            # once this file's tracks are known.
            graph_selection = self._audio_selection if self._hot_audio else None
            if self._hot_audio:
                self._set_lavfi_complex("")
                self._hot_audio = ()
                self._audio_selection = None
            # After the graph is gone, so setting `aid` below cannot fight it.
            if self._pending_languages is not None:
                self._apply_track_languages(self._pending_languages)
                self._pending_languages = None
                self._hot_audio_refused = False  # a new file gets its own chance
            elif graph_selection is not None:
                # A reload with no new preference is the same file again (a
                # reconnect). A switch made through the graph never wrote
                # `aid`, so without this the reload would open on the
                # preferred or default track instead of the one the viewer
                # chose. A hand-picked `aid` carries over on its own.
                self._mpv.aid = graph_selection
            # Start this file on the short initial wait; its first frame puts
            # the long one back (_on_playback_restart).
            _set_option(self._mpv, "cache-pause-wait", _CACHE_PAUSE_WAIT_START)
            # Per load: a direct URL gets the short timeout, the proxy the
            # long one (see _PROXIED_NETWORK_TIMEOUT_S).
            _set_option(
                self._mpv,
                "network-timeout",
                _PROXIED_NETWORK_TIMEOUT_S
                if url.startswith(_LOOPBACK_PREFIXES)
                else _NETWORK_TIMEOUT_S,
            )
            # Demuxer options are read when the file opens, so this is per
            # load: one copy of the stream, not two (_UPSTREAM_CACHED_OPTIONS).
            for name, value in (
                _UPSTREAM_CACHED_OPTIONS if upstream_cached else self._cache_defaults
            ):
                _set_option(self._mpv, name, value)
            self._play_started = time.monotonic()
            self._opened_after = None
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

    def video_size(self) -> tuple[int, int]:
        """The frame size after mpv's own scaling/rotation — dwidth/dheight,
        not the container's width/height, because a rotated or anamorphic
        stream displays at a different shape than it is stored in. Both are
        None until the first frame decodes; (0, 0) says "not known yet"."""
        width = self._prop("dwidth")
        height = self._prop("dheight")
        try:
            return (int(width), int(height))
        except (TypeError, ValueError):
            return (0, 0)

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

    def chapters(self) -> list[tuple[float, str]]:
        # chapter-list: [{"title": ..., "time": ...}], filled when the file
        # opens. A chapter without a title keeps its place with "".
        chapters: list[tuple[float, str]] = []
        try:
            for chapter in self._prop("chapter_list") or []:
                start = chapter.get("time")
                if isinstance(start, (int, float)) and not isinstance(start, bool):
                    chapters.append((float(start), str(chapter.get("title") or "")))
        except Exception:
            return []
        return sorted(chapters)

    def media_format(self) -> MediaFormat:
        """mpv's track list carries everything but the transfer, which is a
        property of the decoded picture (`video-params/gamma`), known once
        the first frame is."""
        try:
            tracks = [dict(track) for track in self._mpv.track_list]
            params = self._mpv.video_params or {}
        except Exception:
            return MediaFormat()
        gamma = params.get("gamma") if isinstance(params, dict) else None
        if gamma in ("pq", "hlg"):
            for track in tracks:
                if track.get("type") == "video" and track.get("selected"):
                    track["color-transfer"] = gamma
        return track_list.media_format(tracks)

    def video_dolby_vision_profile(self) -> int:
        """DV profile of the selected video track (0 = none / not known yet).

        Profile 5 is the one that matters: it stores IPT-C2 colour, only
        libplacebo converts it, and libmpv's render API runs the older vo_gpu
        renderer -- so those files play magenta here whatever the platform
        does. The number comes from mpv itself rather than from the addon's
        label, which says "DV" for profile 8 too, and profile 8 is fine.
        """
        try:
            for track in self._mpv.track_list:
                if track.get("type") != "video" or not track.get("selected"):
                    continue
                profile = track.get("dolby-vision-profile")
                return int(profile) if profile is not None else 0
        except Exception:
            return 0
        return 0

    def decode_report(self) -> DecodeReport:
        """Codec, frame height and dropped frames for what is playing now.

        Both drop counters are summed on purpose: `frame-drop-count` is frames
        the output stage threw away to keep up with the clock, and
        `decoder-frame-drop-count` frames the decoder itself never finished.
        Either one means this machine is behind on this file, and which stage
        gave up first is not something the source list can act on differently.

        `video-codec` is mpv's descriptive string ("HEVC (High Efficiency
        Video Coding)"), not a tidy identifier -- normalising it is the
        application layer's job, since only it knows what buckets it wants.
        """
        codec = self._prop("video-codec") or ""
        _, height = self.video_size()

        def _count(name: str) -> int:
            try:
                return max(0, int(self._prop(name) or 0))
            except (TypeError, ValueError):
                return 0

        return DecodeReport(
            codec=str(codec),
            height=height,
            dropped_frames=_count("frame-drop-count") + _count("decoder-frame-drop-count"),
        )

    def download_speed(self) -> float:
        # cache-speed: bytes/s the demuxer is currently reading from the
        # network. Note what this is NOT: once the cache is full mpv reads at
        # roughly the file's own bitrate, so a single reading is a floor on the
        # connection, never its capacity. Sustained capacity only shows in the
        # fast stretches, which is why the estimator takes a high percentile of
        # many samples rather than believing any one of them.
        value = self._prop("cache_speed")
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def buffered_to(self) -> float:
        # demuxer-cache-time: the absolute timestamp the demuxer has data up
        # to. Unavailable (None) before the file opens and for local files.
        value = self._prop("demuxer_cache_time")
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    # --- tracks ---

    def prepare_track_switching(self) -> None:
        """Put every audio track of this file in the filter graph, once.

        Called from the GUI thread when the track list arrives, never from
        mpv's own event thread: setting a property there blocks the thread mpv
        delivers events on. Nothing depends on it running -- a build without
        lavfi, or a file with more tracks than _MAX_HOT_AUDIO_TRACKS, just
        keeps mpv's plain `aid` switching.
        """
        if self._hot_audio or self._hot_audio_refused:
            return
        track_ids = [track_id for track_id, _ in self._tracks("audio")]
        if not 2 <= len(track_ids) <= _MAX_HOT_AUDIO_TRACKS:
            return
        selected = self._current_track("aid")
        if selected not in track_ids:
            # No audio playing yet (or none at all): a graph built now would
            # pick a track mpv did not.
            return
        if not self._set_lavfi_complex(_audio_graph(selected, track_ids)):
            return
        self._hot_audio = tuple(track_ids)
        self._audio_selection = selected
        _log.info("audio tracks %s held decoded for instant switching", list(track_ids))
        self._reselect_subtitle()

    def _reselect_subtitle(self) -> None:
        """Select the file's subtitle track again after the graph goes in.

        Installing the graph this early leaves an embedded track selected
        but silent for the rest of the file: `sid` still names it and
        `sub-text` stays empty. A seek does not bring it back, and selecting
        it again does. Measured on 0.41 against a six-audio-track MKV (South
        Park S6E1, SubRip): no line in 25s, resumed or from the start, and
        with this every line on time. The re-read is not free: first frame
        came at 7.5-8.0s against 6.0-6.4s without it, on a direct link.
        """
        track = self._current_track("sid")
        if track is None:
            return
        try:
            self._mpv.sid = "no"
            self._mpv.sid = track
        except Exception as exc:
            _log.info("could not reselect subtitle track %s: %r", track, exc)

    def _on_queue_overflow(self) -> None:
        """mpv's demuxer queues overflowed. Called on mpv's own thread, where
        setting a property would block the thread delivering events, so the
        graph comes down on a thread of its own."""
        if not self._hot_audio:
            return
        threading.Thread(target=self._drop_audio_graph, name="mpv-audio-graph", daemon=True).start()

    def _drop_audio_graph(self) -> None:
        """Back to plain `aid` switching for the rest of this file, on the
        track that was playing. The graph is an optimisation for instant track
        switches, and a switch that re-buffers beats a picture that stops."""
        with self._graph_lock:
            if not self._hot_audio:
                return
            selected = self._audio_selection
            self._set_lavfi_complex("")
            self._hot_audio = ()
            self._audio_selection = None
            self._hot_audio_refused = True
            if selected is not None:
                with contextlib.suppress(Exception):
                    self._mpv.aid = selected
        _log.warning(
            "demuxer queues overflowed with every audio track held decoded; switching "
            "audio tracks the ordinary way for the rest of this file (track %s)",
            selected,
        )

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return self._tracks("sub")

    def set_subtitle_track(self, track_id: int | None) -> None:
        if track_id is None:
            # "Off" hides the track instead of dropping it (`sid=no`), because
            # dropping it frees its packets and switching subtitles back on
            # would then re-read the stream from the current position -- 2.2s
            # measured at 5x the file's bitrate. Hidden, it stays in the cache
            # and comes back in the same frame.
            self._mpv.sub_visibility = False
            return
        self._mpv.sub_visibility = True
        if self._current_track("sid") != track_id:
            self._mpv.sid = track_id

    def set_subtitle_delay(self, seconds: float) -> None:
        # sub-delay: applied at render time, nothing is re-read.
        self._mpv.sub_delay = seconds

    def subtitle_delay(self) -> float:
        value = self._prop("sub_delay")
        return float(value) if isinstance(value, (int, float)) else 0.0

    def choose_fallback_subtitle(self) -> bool:
        """mpv chooses subtitles by language TAG, and plenty of files leave
        the tag out ("und", or nothing) while the title says "English". With
        a language preferred and nothing chosen, take the track
        track_list.fallback_subtitle names. It names none when a track is
        tagged with the language: mpv chooses that itself, and nothing
        selected yet only means it has not got that far (the track list
        comes before the choice)."""
        wanted = self._subtitle_language
        if self._current_track("sid") is not None:
            return False
        pick = track_list.fallback_subtitle(self._sub_tracks(), wanted)
        if pick is None:
            return False
        self._mpv.sid = pick["id"]
        self._mpv.sub_visibility = True
        _log.info(
            "subtitles: no track tagged %r; chose track %s (%r) by its title",
            wanted,
            pick["id"],
            pick.get("title"),
        )
        return True

    def has_preferred_subtitle(self) -> bool:
        """Whether the file carries a full subtitle track in the preferred
        language: tagged with it, or untagged and titled with its name. Read
        off the track list, never the selection, which mpv makes only after
        it has listed the tracks. A forced-only track does not count: it
        shows the foreign-language lines and nothing else."""
        return track_list.has_full_track(self._sub_tracks(), self._subtitle_language)

    def _sub_tracks(self) -> list[dict[str, Any]]:
        try:
            return [t for t in self._mpv.track_list if t.get("type") == "sub"]
        except Exception:
            return []

    def add_subtitle(self, url: str, title: str, lang: str, *, select: bool = True) -> None:
        # mpv reads the whole file now, which is what makes every later switch
        # to or from it free. "auto" adds it without taking the selection.
        self._mpv.command("sub-add", url, "select" if select else "auto", title, lang)
        if select:
            self._mpv.sub_visibility = True

    def audio_tracks(self) -> list[tuple[int, str]]:
        return self._tracks("audio")

    def set_audio_track(self, track_id: int | None) -> None:
        if track_id is not None and track_id in self._hot_audio:
            if self._set_lavfi_complex(_audio_graph(track_id, self._hot_audio)):
                self._audio_selection = track_id
                return
            # The graph is not working after all; stop trusting it for good.
            self._hot_audio = ()
            self._audio_selection = None
        elif self._hot_audio:
            # A track the graph does not name, or audio off entirely: the graph
            # has to go first, or `aid` fights it for the selection.
            self._set_lavfi_complex("")
            self._hot_audio = ()
            self._audio_selection = None
        self._mpv.aid = "no" if track_id is None else track_id

    def current_subtitle_track(self) -> int | None:
        if self._prop("sub_visibility") is False:
            return None
        return self._current_track("sid")

    def current_audio_track(self) -> int | None:
        # With a graph installed `aid` reports "no": the graph owns the
        # selection, so the id it was built with is the answer.
        if self._hot_audio:
            return self._audio_selection
        return self._current_track("aid")

    def _set_lavfi_complex(self, graph: str) -> bool:
        """Install a filter graph, reporting whether mpv took it."""
        try:
            self._mpv.lavfi_complex = graph
        except Exception as exc:
            _log.info("lavfi-complex %r rejected (%s); using plain track switching", graph, exc)
            return False
        return True

    def set_track_languages(self, languages: TrackLanguages) -> None:
        # Held for play(): applied here, resetting `aid` would re-select a
        # track in the file that is still playing.
        self._pending_languages = languages

    def _apply_track_languages(self, preference: TrackLanguages) -> None:
        """Let mpv choose this file's tracks by language when it opens it.

        mpv does the choosing itself at load, so a preferred track costs
        nothing -- no switch after the fact, no refresh seek. Two things have
        to be true for it to happen, and neither is by default. The lists go
        in every spelling a track may be tagged with (languages.track_codes),
        since only newer builds match `de` against `ger`. And `aid`/`sid` go
        back to `auto`: a track picked by hand is written to the option, and
        mpv carries that id into the next file, where it outranks
        alang/slang -- measured on 0.41, `aid=1` picked in one file played
        track 1 in the next with `alang=de` set.
        """
        self._subtitle_language = preference.subtitle
        # A new file: the last one's timing correction is not this one's.
        with contextlib.suppress(Exception):
            self._mpv.sub_delay = 0
        _set_option(self._mpv, "alang", ",".join(languages.track_codes(preference.audio)))
        subtitles_off = preference.subtitle == languages.SUBTITLES_OFF
        _set_option(
            self._mpv,
            "slang",
            "" if subtitles_off else ",".join(languages.track_codes(preference.subtitle)),
        )
        self._mpv.aid = "auto"
        self._mpv.sid = "auto"
        # "Off" hides rather than deselects, for the reason set_subtitle_track
        # gives: turning them back on mid-film is then instant. With no
        # preference the visibility is left as the viewer last set it.
        if subtitles_off:
            self._mpv.sub_visibility = False
        elif preference.subtitle:
            self._mpv.sub_visibility = True

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
        return track_list.labelled(self._mpv.track_list, kind)

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

    def set_stream_ended_callback(self, callback: Callable[[], None] | None) -> None:
        """Fire when mpv stops feeding frames — a finished file, or a dead
        socket that looked like one.

        `eof-reached` is the only signal a dropped connection produces. ffmpeg
        reports the truncated read as end of file rather than as an error, so
        mpv finishes the file and (under keep-open) holds the last frame,
        paused. Whether that was the real end is a question about the position
        it stopped at, which the caller is the one holding — this only reports
        that playback ended.
        """
        self._stream_ended_callback = callback
        if callback is None or self._eof_observer_registered:
            return
        with contextlib.suppress(Exception):
            self._mpv.observe_property("eof-reached", self._on_eof)
            self._eof_observer_registered = True

    def set_load_failed_callback(self, callback: Callable[[], None] | None) -> None:
        """Fire when a URL never opened.

        `eof-reached` cannot carry this one. It says the file that was playing
        has run out, and a file that never started playing never sets it: mpv
        answers a URL it could not open with an END_FILE event whose reason is
        ERROR and then goes idle, silently as far as any property observer is
        concerned. That is what a refused CDN node looks like from here -- and
        without this, the spinner above it stays up forever, because nothing
        ends and nothing errors.

        Registered on the event stream rather than through a property for the
        same reason, so the callback arrives on mpv's own thread like the
        others do.
        """
        self._load_failed_callback = callback
        if callback is None or self._end_file_observer_registered:
            return
        with contextlib.suppress(Exception):
            self._mpv.event_callback("end-file")(self._on_end_file)
            self._end_file_observer_registered = True

    def render_handle(self) -> object | None:
        handle: object = self._mpv
        return handle

    def _on_eof(self, _name: str, value: object) -> None:
        # Fires on mpv's thread, and again with False on the next load.
        if value and self._stream_ended_callback is not None:
            self._stream_ended_callback()

    def _on_end_file(self, event: object) -> None:
        # Fires on mpv's thread, for EVERY way a file can end -- the ones that
        # are nobody's problem included (stop() aborts one, a new play()
        # replaces one). Only ERROR is a failure to report.
        if self._load_failed_callback is None or _end_file_reason(event) != _END_FILE_ERROR:
            return
        self._load_failed_callback()

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
