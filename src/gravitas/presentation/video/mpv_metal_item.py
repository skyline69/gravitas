"""Zero-copy video item for macOS: mpv renders on the GPU, Qt draws the result.

mpv draws through OpenGL into an IOSurface that Metal samples directly (see
native/macos/), so a decoded frame goes GPU to GPU with nothing crossing the
CPU. That is the difference from mpv_sw_item.py, the fallback used when the
native bridge is missing or was built against a different Qt: there every
frame is converted and uploaded by hand.

This file is deliberately thin. mpv's render context, the GL context, the
surface and all of their teardown live in the C++ bridge, because their
lifetimes cannot be expressed from Python: freeing them belongs on Qt's render
thread, Qt announces that moment through render-thread signals, and a Python
slot on one of those has to take the GIL while the GUI thread is holding it
and waiting for the render thread. Both sides stop forever. In C++ none of
that applies, so the bridge tears itself down when the window's scene graph
goes away and Python is left with three calls: size it, render, hand back the
texture.

What remains here is the one thing Qt insists is the item's: the node.
"""

from __future__ import annotations

import ctypes
import logging
from typing import Any

from PySide6.QtCore import Property, Slot
from PySide6.QtQuick import QQuickItem, QQuickWindow, QSGNode, QSGSimpleTextureNode, QSGTexture
from shiboken6 import getCppPointer, wrapInstance

from gravitas.presentation.video.geometry import fitted_rect
from gravitas.presentation.video.metal_bridge import BridgeUnavailable, last_error, library

_log = logging.getLogger(__name__)


def mpv_pointer(handle: Any) -> int:
    """The raw mpv_handle* behind python-mpv's wrapper.

    The bridge talks to libmpv directly, so it needs the pointer rather than
    the Python object around it.
    """
    try:
        return int(ctypes.cast(handle.handle, ctypes.c_void_p).value or 0)
    except Exception:  # not an MPV instance, or already torn down
        return 0


# One bridge per window, keyed by the window's pointer. It outlives every item
# that draws through it: the player page is destroyed and rebuilt each time
# something is played, while the mpv core -- and libmpv allows exactly one
# render context per core -- lives for the whole session.
_BRIDGES: dict[int, int] = {}


class MpvMetalVideoItem(QQuickItem):
    """Same contract as the other two video items: set `handle`, get video."""

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._handle: Any = None
        self._failed = False
        self._size: tuple[int, int] = (0, 0)

    # --- the handle PlayerController injects ---

    def _get_handle(self) -> Any:
        return self._handle

    def _set_handle(self, handle: Any) -> None:
        self._handle = handle
        self.update()

    handle = Property("QVariant", _get_handle, _set_handle)  # type: ignore[arg-type]

    @Slot()
    def requestUpdate(self) -> None:
        """mpv has a frame. Called by the bridge, on the GUI thread.

        It has to be QQuickItem::update(): only that marks the item dirty, and
        Qt calls updatePaintNode for dirty items alone. Asking the window to
        update instead schedules a render that skips this item entirely, so
        the video sits still until something else (a resize) dirties it.
        """
        self.update()

    # --- rendering ---

    def _video_size(self) -> tuple[int, int]:
        try:
            return int(self._handle.dwidth or 0), int(self._handle.dheight or 0)
        except Exception:  # no file loaded yet, or mpv is gone
            return 0, 0

    def _fail(self, message: str) -> None:
        """Give up on the zero-copy path for this item, loudly and once."""
        self._failed = True
        _log.warning("%s; this player will not render", message)

    def updatePaintNode(  # type: ignore[override]  # C++ returns a nullable pointer
        self, node: QSGNode | None, _data: Any
    ) -> QSGNode | None:
        window = self.window()
        if window is None or self._handle is None or self._failed:
            return node
        try:
            bridge = library()
        except BridgeUnavailable:
            self._failed = True
            return node
        handle = self._bridge(bridge, window)
        if handle is None:
            return node

        video_w, video_h = self._video_size()
        if not video_w or not video_h:
            # mpv has not decoded anything yet. Keep whatever is on screen
            # rather than flashing a black frame.
            return node
        if (video_w, video_h) != self._size:
            if not bridge.gv_video_bridge_set_size(ctypes.c_void_p(handle), video_w, video_h):
                self._fail(f"zero-copy surface failed ({last_error(bridge)})")
                return node
            self._size = (video_w, video_h)
            pixel_format = (bridge.gv_video_bridge_format(ctypes.c_void_p(handle)) or b"").decode()
            _log.info("zero-copy video surface at %dx%d, %s", video_w, video_h, pixel_format)

        if not bridge.gv_video_bridge_render(ctypes.c_void_p(handle)):
            # Routine while the scene graph is between generations: keep the
            # node as it is and wait for the next frame.
            _log.debug("no frame rendered (%s)", last_error(bridge))
            return node
        address = bridge.gv_video_bridge_texture(ctypes.c_void_p(handle))
        if not address:
            return node

        texture_node = node if isinstance(node, QSGSimpleTextureNode) else QSGSimpleTextureNode()
        # The bridge owns the texture and outlives the node; letting the node
        # own it would free it out from under the renderer.
        texture_node.setOwnsTexture(False)
        # wrapInstance hands back a shiboken Object; it IS the bridge's
        # QSGTexture, but the stubs cannot know that.
        texture_node.setTexture(wrapInstance(address, QSGTexture))  # type: ignore[arg-type]
        texture_node.setRect(fitted_rect(self.width(), self.height(), video_w, video_h))
        texture_node.setFiltering(QSGTexture.Filtering.Linear)
        return texture_node

    def _bridge(self, bridge: Any, window: QQuickWindow) -> int | None:
        """This window's bridge, created on first use and reused after that.

        Reuse is not an optimisation: libmpv allows one render context per
        core, and the core outlives the player page. A bridge per item makes
        the second playback fail outright.
        """
        key = getCppPointer(window)[0]
        existing = _BRIDGES.get(key)
        if existing is not None and bridge.gv_video_bridge_stale(ctypes.c_void_p(existing)):
            # The scene graph it belonged to is gone -- going fullscreen
            # recreates it -- so its textures belong to a renderer that no
            # longer exists. A new one is built against the new graph, and the
            # old generation is released as part of that.
            del _BRIDGES[key]
            existing = None
            self._size = (0, 0)
        if existing is not None:
            # Whichever item is on screen is the one to wake; the page is
            # rebuilt for every playback while the bridge stays.
            bridge.gv_video_bridge_set_item(ctypes.c_void_p(existing), getCppPointer(self)[0])
            return existing
        pointer = mpv_pointer(self._handle)
        if not pointer:
            self._fail("mpv handle is not usable")
            return None
        created = int(bridge.gv_video_bridge_create(key, ctypes.c_void_p(pointer)) or 0)
        if not created:
            self._fail(f"zero-copy bridge unavailable ({last_error(bridge)})")
            return None
        _BRIDGES[key] = created
        bridge.gv_video_bridge_set_item(ctypes.c_void_p(created), getCppPointer(self)[0])
        _log.info("zero-copy video bridge ready")
        return created
