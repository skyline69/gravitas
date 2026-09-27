"""The Qt side of the native engine's zero-copy video on macOS, through ctypes.

The engine renders with Metal on macOS, and can draw straight into textures
the scene graph samples if it works on Qt's own MTLDevice and command queue.
Reaching those, wrapping a texture as a QSGTexture and hearing when the scene
graph goes away are C++ matters (native/macos/gravitas_native_metal.h); this
module declares those entry points and keeps one bridge per window.

Nothing is torn down in the engine when the scene graph goes: Metal objects
are reference counted, so the engine keeps its device and queue alive itself
and the bridge keeps every texture it wraps. A new scene graph gets a new
bridge, and the engine is simply attached to it again.

The entry points live in the same library as the mpv bridge and are loaded
the same guarded way (metal_bridge.library(): present, the ABI this code
knows, built for the running Qt). A library built before they existed lacks
them, and the native engine stays on its readback path.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass
from typing import Any

from gravitas.presentation.video import metal_bridge

_log = logging.getLogger(__name__)


class DeviceInfo(ctypes.Structure):
    """GvNativeMetalDevice: Qt's MTLDevice and command queue."""

    _fields_ = [
        ("device", ctypes.c_uint64),
        ("queue", ctypes.c_uint64),
    ]


_library: ctypes.CDLL | None = None
_checked = False


def library() -> ctypes.CDLL | None:
    """The bridge library with the native entry points bound, or None."""
    global _library, _checked
    if _checked:
        return _library
    _checked = True
    try:
        candidate = metal_bridge.library()
        candidate.gv_native_mtl_error.argtypes = []
        candidate.gv_native_mtl_error.restype = ctypes.c_char_p
        candidate.gv_native_mtl_create.argtypes = [ctypes.c_void_p]
        candidate.gv_native_mtl_create.restype = ctypes.c_void_p
        candidate.gv_native_mtl_device.argtypes = [ctypes.c_void_p, ctypes.POINTER(DeviceInfo)]
        candidate.gv_native_mtl_device.restype = ctypes.c_int
        candidate.gv_native_mtl_texture.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_int,
            ctypes.c_int,
        ]
        candidate.gv_native_mtl_texture.restype = ctypes.c_void_p
        candidate.gv_native_mtl_stale.argtypes = [ctypes.c_void_p]
        candidate.gv_native_mtl_stale.restype = ctypes.c_int
        candidate.gv_native_mtl_destroy.argtypes = [ctypes.c_void_p]
        candidate.gv_native_mtl_destroy.restype = None
    except metal_bridge.BridgeUnavailable:
        return None
    except AttributeError:
        _log.info("the video bridge predates the native engine; rebuild it for zero-copy video")
        return None
    _library = candidate
    return _library


def last_error() -> str:
    lib = library()
    return (lib.gv_native_mtl_error() or b"").decode() if lib is not None else ""


@dataclass
class Bridge:
    """One window's bridge, and the engine attached to it."""

    handle: int
    engine: int = 0

    def attach(self, source: Any) -> None:
        """Hands `source` (the engine's player) Qt's device and queue."""
        lib = library()
        assert lib is not None
        info = DeviceInfo()
        if not lib.gv_native_mtl_device(ctypes.c_void_p(self.handle), ctypes.byref(info)):
            raise RuntimeError(last_error())
        source.attach_metal(device=int(info.device), queue=int(info.queue))

    def texture(self, image: int, width: int, height: int) -> int:
        lib = library()
        assert lib is not None
        texture = lib.gv_native_mtl_texture(ctypes.c_void_p(self.handle), image, width, height)
        return int(texture or 0)

    def stale(self) -> bool:
        lib = library()
        return lib is None or bool(lib.gv_native_mtl_stale(ctypes.c_void_p(self.handle)))

    def destroy(self) -> None:
        lib = library()
        if lib is not None:
            lib.gv_native_mtl_destroy(ctypes.c_void_p(self.handle))


# One bridge per window, keyed by the window's pointer: the player page is
# rebuilt for every playback, while the window -- and its scene graph --
# stays for the session.
_BRIDGES: dict[int, Bridge] = {}


def for_window(window_pointer: int) -> Bridge | None:
    """The window's bridge, created on first use and replaced once stale (a
    new scene graph means new wrappers). Render thread only."""
    lib = library()
    if lib is None:
        return None
    existing = _BRIDGES.get(window_pointer)
    if existing is not None and existing.stale():
        existing.destroy()
        del _BRIDGES[window_pointer]
        existing = None
    if existing is not None:
        return existing
    handle = int(lib.gv_native_mtl_create(ctypes.c_void_p(window_pointer)) or 0)
    if not handle:
        _log.info("no zero-copy video for the native engine (%s)", last_error())
        return None
    bridge = Bridge(handle)
    _BRIDGES[window_pointer] = bridge
    return bridge
