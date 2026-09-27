"""The Qt side of the native engine's zero-copy video, through ctypes.

libplacebo can render straight into an image the scene graph samples only if
it works on Qt's own Vulkan device. Reaching that device, wrapping an image as
a QSGTexture and hearing when the scene graph goes away are C++ matters
(native/linux/gravitas_native_vk.h); this module declares those entry points
and keeps one bridge per window.

The same library creates the Vulkan instance for decoding into the scene
graph's device: the engine makes a device with decode queues on it, and the
window adopts that device before its scene graph starts (`instance`, then
`adopt`; see gravitas_native_vk.h).

On Linux the entry points live in the same library as the mpv bridge, and are
loaded the same guarded way (vulkan_bridge.library(): present, the ABI this
code knows, built for the running Qt). A library built before they existed
simply lacks them, and the native engine stays on its readback path. On
Windows mpv has no bridge, and these are a library of their own
(gravitas_native_vk.dll) with the same three checks.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import PySide6
from PySide6.QtCore import qVersion

from gravitas.presentation.video import vulkan_bridge

_log = logging.getLogger(__name__)


class DeviceInfo(ctypes.Structure):
    """GvNativeVkDevice: Qt's Vulkan device, as libplacebo imports it."""

    _fields_ = [
        ("instance", ctypes.c_uint64),
        ("get_instance_proc_addr", ctypes.c_uint64),
        ("physical_device", ctypes.c_uint64),
        ("device", ctypes.c_uint64),
        ("queue_family", ctypes.c_uint32),
        ("queue_index", ctypes.c_uint32),
        ("api_version", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("features", ctypes.c_uint64),
    ]


class InstanceInfo(ctypes.Structure):
    """GvNativeVkInstance: the instance the scene graph and the engine share."""

    _fields_ = [
        ("instance", ctypes.c_uint64),
        ("get_instance_proc_addr", ctypes.c_uint64),
    ]


# Kept in step with GV_NATIVE_VK_ABI in the header (checked on Windows only).
_ABI = 1
# Windows' library: next to the Python that loads it, like the Linux one.
_WINDOWS_LIBRARY = Path(__file__).parent / "gravitas_native_vk.dll"

_library: ctypes.CDLL | None = None
_checked = False


def _load() -> ctypes.CDLL | None:
    """The library the entry points live in, checked; None (logged) when
    there is none this code may use."""
    if sys.platform != "win32":
        try:
            return vulkan_bridge.library()
        except vulkan_bridge.BridgeUnavailable:
            return None
    if not _WINDOWS_LIBRARY.is_file():
        _log.info("%s not built; the native engine renders through readback", _WINDOWS_LIBRARY.name)
        return None
    try:
        # Its Qt DLLs and MSVC runtime are PySide6's own, in a directory
        # Python does not search for a ctypes load.
        with os.add_dll_directory(str(Path(PySide6.__file__).parent)):
            candidate = ctypes.CDLL(str(_WINDOWS_LIBRARY))
        candidate.gv_native_vk_abi.argtypes = []
        candidate.gv_native_vk_abi.restype = ctypes.c_int
        candidate.gv_native_vk_qt_version.argtypes = []
        candidate.gv_native_vk_qt_version.restype = ctypes.c_char_p
    except (OSError, AttributeError) as exc:
        _log.info("cannot load %s: %s", _WINDOWS_LIBRARY.name, exc)
        return None
    abi = candidate.gv_native_vk_abi()
    if abi != _ABI:
        _log.info("%s has ABI %d, expected %d; rebuild it", _WINDOWS_LIBRARY.name, abi, _ABI)
        return None
    built_for = (candidate.gv_native_vk_qt_version() or b"").decode()
    if built_for != qVersion():
        _log.info(
            "%s was built against Qt %s, running Qt %s; rebuild it",
            _WINDOWS_LIBRARY.name,
            built_for,
            qVersion(),
        )
        return None
    return candidate


def library() -> ctypes.CDLL | None:
    """The bridge library with the native entry points bound, or None."""
    global _library, _checked
    if _checked:
        return _library
    _checked = True
    candidate = _load()
    if candidate is None:
        return None
    try:
        candidate.gv_native_vk_error.argtypes = []
        candidate.gv_native_vk_error.restype = ctypes.c_char_p
        candidate.gv_native_vk_create.argtypes = [ctypes.c_void_p]
        candidate.gv_native_vk_create.restype = ctypes.c_void_p
        candidate.gv_native_vk_device.argtypes = [ctypes.c_void_p]
        candidate.gv_native_vk_device.restype = ctypes.POINTER(DeviceInfo)
        # The callback is a raw function address from the engine, never a
        # Python callable: it runs on Qt's render thread without the GIL.
        candidate.gv_native_vk_on_invalidate.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        candidate.gv_native_vk_on_invalidate.restype = None
        candidate.gv_native_vk_texture.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_int,
            ctypes.c_int,
        ]
        candidate.gv_native_vk_texture.restype = ctypes.c_void_p
        candidate.gv_native_vk_stale.argtypes = [ctypes.c_void_p]
        candidate.gv_native_vk_stale.restype = ctypes.c_int
        candidate.gv_native_vk_destroy.argtypes = [ctypes.c_void_p]
        candidate.gv_native_vk_destroy.restype = None
        candidate.gv_native_vk_instance.argtypes = [ctypes.POINTER(InstanceInfo)]
        candidate.gv_native_vk_instance.restype = ctypes.c_int
        candidate.gv_native_vk_adopt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        candidate.gv_native_vk_adopt.restype = ctypes.c_int
    except AttributeError:
        _log.info("the video bridge predates the native engine; rebuild it for zero-copy video")
        return None
    _library = candidate
    return _library


def last_error() -> str:
    lib = library()
    return (lib.gv_native_vk_error() or b"").decode() if lib is not None else ""


def instance() -> tuple[int, int] | None:
    """`(VkInstance, vkGetInstanceProcAddr)` of the instance the window and
    the engine's shared device use, created on first call; None (logged)
    when there is none. GUI thread, before the scene graph starts."""
    lib = library()
    if lib is None:
        return None
    info = InstanceInfo()
    if not lib.gv_native_vk_instance(ctypes.byref(info)):
        _log.info("no Vulkan instance for a shared device (%s)", last_error())
        return None
    return int(info.instance), int(info.get_instance_proc_addr)


def gpu_available() -> bool:
    """Whether the native engine can render on Vulkan here: the library
    loads, the instance can be made, and a GPU (not a CPU rasteriser) offers
    Vulkan 1.2. Asked before the scene graph's API is chosen -- the last
    moment another one can be -- and logged either way."""
    lib = library()
    if lib is None:
        return False
    # Bound here, not in library(): only Windows asks, and a Linux bridge
    # built before it existed must keep its zero-copy path.
    try:
        probe = lib.gv_native_vk_gpu
    except AttributeError:
        _log.info("the Vulkan bridge predates the GPU probe; rebuild it")
        return False
    probe.argtypes = []
    probe.restype = ctypes.c_int
    if not probe():
        _log.info("no Vulkan GPU for the native engine (%s)", last_error())
        return False
    return True


def adopt(window_pointer: int, handles: Mapping[str, int]) -> bool:
    """Has the window render with the engine's shared device (`handles` as
    `SharedDevice.handles()` gives them). GUI thread, before the window's
    scene graph starts."""
    lib = library()
    if lib is None:
        return False
    return bool(
        lib.gv_native_vk_adopt(
            ctypes.c_void_p(window_pointer),
            handles["physical_device"],
            handles["device"],
            handles["queue_family"],
            handles["queue_index"],
        )
    )


@dataclass
class Bridge:
    """One window's bridge, and the engine attached to it."""

    handle: int
    engine: int = 0

    def device(self) -> dict[str, int]:
        lib = library()
        assert lib is not None
        info = lib.gv_native_vk_device(ctypes.c_void_p(self.handle)).contents
        fields = (field[0] for field in DeviceInfo._fields_ if field[0] != "reserved")
        return {name: int(getattr(info, name)) for name in fields}

    def attach(self, source: Any) -> None:
        """Hands `source` (the engine's player) Qt's Vulkan device, and has
        the teardown it returns called when the scene graph goes."""
        callback, context = source.attach_vulkan(**self.device())
        self.on_invalidate(callback, context)

    def on_invalidate(self, callback: int, context: int) -> None:
        lib = library()
        assert lib is not None
        lib.gv_native_vk_on_invalidate(
            ctypes.c_void_p(self.handle), ctypes.c_void_p(callback), ctypes.c_void_p(context)
        )

    def texture(self, image: int, width: int, height: int) -> int:
        lib = library()
        assert lib is not None
        texture = lib.gv_native_vk_texture(ctypes.c_void_p(self.handle), image, width, height)
        return int(texture or 0)

    def stale(self) -> bool:
        lib = library()
        return lib is None or bool(lib.gv_native_vk_stale(ctypes.c_void_p(self.handle)))

    def destroy(self) -> None:
        lib = library()
        if lib is not None:
            lib.gv_native_vk_destroy(ctypes.c_void_p(self.handle))


# One bridge per window, keyed by the window's pointer: the player page is
# rebuilt for every playback, while the window -- and its Vulkan device --
# stays for the session.
_BRIDGES: dict[int, Bridge] = {}


def for_window(window_pointer: int) -> Bridge | None:
    """The window's bridge, created on first use and replaced once stale (a
    new scene graph means a new device). Render thread only."""
    lib = library()
    if lib is None:
        return None
    existing = _BRIDGES.get(window_pointer)
    if existing is not None and existing.stale():
        # Its textures and the engine's images went with the old scene graph.
        existing.destroy()
        del _BRIDGES[window_pointer]
        existing = None
    if existing is not None:
        return existing
    handle = int(lib.gv_native_vk_create(ctypes.c_void_p(window_pointer)) or 0)
    if not handle:
        _log.info("no zero-copy video for the native engine (%s)", last_error())
        return None
    bridge = Bridge(handle)
    _BRIDGES[window_pointer] = bridge
    return bridge
