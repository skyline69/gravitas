"""ctypes loader for the macOS zero-copy video bridge.

The bridge (native/macos/) is a binary compiled against one exact Qt. Loading
it against a different one would not fail cleanly -- QRhi has no binary
compatibility guarantee, so the first frame would crash somewhere inside
Metal. Everything here exists to make that impossible: the library is only
used if it is present, has the ABI this code knows, and reports the same Qt
version the process is actually running.

Any of those failing is not an error. The caller falls back to libmpv's
software render path, which is what non-macOS builds never leave anyway.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtCore import qVersion

_log = logging.getLogger(__name__)

# Kept in step with GV_VIDEO_BRIDGE_ABI in the header.
_ABI = 6
_LIBRARY = Path(__file__).parent / "libgravitas_video_bridge.dylib"


class BridgeUnavailable(Exception):
    """The zero-copy path cannot be used; the caller falls back to software."""


def _bind(library: ctypes.CDLL) -> ctypes.CDLL:
    """Declare every signature. ctypes defaults to c_int returns, which
    truncates 64-bit pointers -- on this boundary that is a wild pointer, not
    a wrong number."""
    library.gv_video_bridge_abi.argtypes = []
    library.gv_video_bridge_abi.restype = ctypes.c_int
    library.gv_video_bridge_qt_version.argtypes = []
    library.gv_video_bridge_qt_version.restype = ctypes.c_char_p
    library.gv_video_bridge_error.argtypes = []
    library.gv_video_bridge_error.restype = ctypes.c_char_p
    library.gv_video_bridge_create.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.gv_video_bridge_create.restype = ctypes.c_void_p
    library.gv_video_bridge_destroy.argtypes = [ctypes.c_void_p]
    library.gv_video_bridge_destroy.restype = None
    library.gv_video_bridge_set_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    library.gv_video_bridge_set_size.restype = ctypes.c_int
    library.gv_video_bridge_set_item.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.gv_video_bridge_set_item.restype = None
    library.gv_video_bridge_render.argtypes = [ctypes.c_void_p]
    library.gv_video_bridge_render.restype = ctypes.c_int
    library.gv_video_bridge_format.argtypes = [ctypes.c_void_p]
    library.gv_video_bridge_format.restype = ctypes.c_char_p
    library.gv_video_bridge_texture.argtypes = [ctypes.c_void_p]
    library.gv_video_bridge_texture.restype = ctypes.c_void_p
    return library


_library: ctypes.CDLL | None = None
_load_failure: str | None = None


def library() -> ctypes.CDLL:
    """The loaded bridge, or BridgeUnavailable with the reason.

    Cached both ways: a missing or mismatched library will not become present
    mid-session, and the reason is worth logging exactly once.
    """
    global _library, _load_failure
    if _library is not None:
        return _library
    if _load_failure is not None:
        raise BridgeUnavailable(_load_failure)

    def unavailable(reason: str) -> BridgeUnavailable:
        global _load_failure
        _load_failure = reason
        _log.info("zero-copy video unavailable (%s); rendering video in software", reason)
        return BridgeUnavailable(reason)

    if sys.platform != "darwin":
        raise unavailable("not macOS")
    if not _LIBRARY.is_file():
        raise unavailable(f"{_LIBRARY.name} not built")
    try:
        candidate = _bind(ctypes.CDLL(str(_LIBRARY)))
    except OSError as exc:
        raise unavailable(f"cannot load {_LIBRARY.name}: {exc}") from exc
    except AttributeError as exc:  # an older build missing an entry point
        raise unavailable(f"{_LIBRARY.name} is missing an entry point: {exc}") from exc

    abi = candidate.gv_video_bridge_abi()
    if abi != _ABI:
        raise unavailable(f"ABI {abi}, expected {_ABI} -- rebuild the bridge")
    built_for = (candidate.gv_video_bridge_qt_version() or b"").decode()
    if built_for != qVersion():
        raise unavailable(f"built against Qt {built_for}, running Qt {qVersion()}")

    _log.info("zero-copy video bridge loaded (Qt %s)", built_for)
    _library = candidate
    return _library


def available() -> bool:
    try:
        library()
    except BridgeUnavailable:
        return False
    return True


def last_error(library_: ctypes.CDLL) -> str:
    return (library_.gv_video_bridge_error() or b"").decode()
