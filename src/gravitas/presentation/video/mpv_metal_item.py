"""Zero-copy video item for macOS: mpv renders on the GPU, Qt draws the result.

mpv draws through OpenGL into an IOSurface that Metal samples directly (see
native/macos/), so a decoded frame goes GPU to GPU with nothing crossing the
CPU. That is the difference from mpv_sw_item.py, the fallback used when the
native bridge is missing or was built against a different Qt: there every
frame is converted and uploaded by hand.

The order of operations is forced, and it is not the obvious one:

  1. The bridge, and its GL context, is created as soon as there is a window
     and an mpv handle -- BEFORE anything is known about the video.
  2. mpv's render context is created on that GL context immediately after. It
     has to exist before mpv can bring up its video output at all, and until
     it does mpv cannot report a resolution.
  3. Only then does a frame arrive, and with it the video's size, which the
     bridge turns into a surface.

Everything here runs on the render thread (inside updatePaintNode, or a
directly-connected sceneGraphInvalidated handler). Nothing is freed while the
window lives: Qt's batch renderer keeps using a texture for an unbounded
number of frames after the node referencing it is gone, so a surface replaced
by a resolution change is retired inside the bridge instead.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
from typing import Any

from PySide6.QtCore import Property, QRectF, Qt
from PySide6.QtQuick import QQuickItem, QQuickWindow, QSGNode, QSGSimpleTextureNode, QSGTexture
from shiboken6 import getCppPointer, wrapInstance

from gravitas.presentation.video.metal_bridge import BridgeUnavailable, last_error, library
from gravitas.presentation.video.mpv_item import _macos_gl_symbol, _UpdateBridge

_log = logging.getLogger(__name__)


def fitted_rect(item_width: float, item_height: float, video_w: int, video_h: int) -> QRectF:
    """The video's rect inside the item, preserving aspect (letterboxed).

    mpv renders at the video's own resolution here, so unlike the software
    path there is no buffer size to choose -- only where to put the quad.
    """
    if item_width <= 0 or item_height <= 0 or video_w <= 0 or video_h <= 0:
        return QRectF(0, 0, max(item_width, 0.0), max(item_height, 0.0))
    scale = min(item_width / video_w, item_height / video_h)
    width = video_w * scale
    height = video_h * scale
    return QRectF((item_width - width) / 2.0, (item_height - height) / 2.0, width, height)


class MpvMetalVideoItem(QQuickItem):
    """Same contract as the other two video items: set `handle`, get video."""

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._handle: Any = None
        self._bridge: int | None = None
        self._ctx: Any = None
        self._size: tuple[int, int] = (0, 0)
        self._failed = False
        self._connected_window: QQuickWindow | None = None
        self._update_bridge = _UpdateBridge(self)
        # mpv calls this for as long as its render context lives, so the
        # ctypes trampoline must outlive the call that installs it.
        self._proc_resolver: Any = None

    # --- the handle PlayerController injects ---

    def _get_handle(self) -> Any:
        return self._handle

    def _set_handle(self, handle: Any) -> None:
        self._handle = handle
        self.update()

    handle = Property("QVariant", _get_handle, _set_handle)  # type: ignore[arg-type]

    def scheduleUpdate(self) -> None:
        # mpv's thread: touch nothing but the update bridge, which survives
        # this item's death.
        self._update_bridge.schedule()

    # --- teardown ---

    def _connect_window(self, window: QQuickWindow) -> None:
        if self._connected_window is window:
            return
        self._connected_window = window
        # Direct connection so this runs ON the render thread, where the
        # resources live; a queued one would land on the GUI thread and crash.
        window.sceneGraphInvalidated.connect(self._release, Qt.ConnectionType.DirectConnection)

    def _release(self) -> None:
        """Render thread, scene graph gone: the one moment freeing is safe."""
        self._free_render_context()
        handle, self._bridge = self._bridge, None
        if handle is None:
            return
        with contextlib.suppress(BridgeUnavailable):
            library().gv_video_bridge_destroy(ctypes.c_void_p(handle))

    def _free_render_context(self) -> None:
        ctx, self._ctx = self._ctx, None
        if ctx is None:
            return
        with contextlib.suppress(Exception):
            # Detach first: no callback can be in flight while free() runs.
            ctx.update_cb = None
        with contextlib.suppress(Exception):
            ctx.free()

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
        self._connect_window(window)
        if not self._ensure_bridge(bridge, window):
            return node
        if not self._ensure_render_context(bridge):
            return node

        video_w, video_h = self._video_size()
        if not video_w or not video_h:
            # mpv has not decoded anything yet. Keep whatever is on screen
            # rather than flashing a black frame.
            return node
        if (video_w, video_h) != self._size:
            if not bridge.gv_video_bridge_set_size(ctypes.c_void_p(self._bridge), video_w, video_h):
                self._fail(f"zero-copy surface failed ({last_error(bridge)})")
                return node
            self._size = (video_w, video_h)
            pixel_format = (
                bridge.gv_video_bridge_format(ctypes.c_void_p(self._bridge)) or b""
            ).decode()
            _log.info("zero-copy video surface at %dx%d, %s", video_w, video_h, pixel_format)

        if not self._render_frame(bridge):
            return node
        address = bridge.gv_video_bridge_texture(ctypes.c_void_p(self._bridge))
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

    def _ensure_bridge(self, bridge: Any, window: QQuickWindow) -> bool:
        if self._bridge is not None:
            return True
        handle = bridge.gv_video_bridge_create(getCppPointer(window)[0])
        if not handle:
            self._fail(f"zero-copy bridge unavailable ({last_error(bridge)})")
            return False
        self._bridge = handle
        return True

    def _ensure_render_context(self, bridge: Any) -> bool:
        if self._ctx is not None:
            return True
        import mpv  # type: ignore[import-untyped]

        if not bridge.gv_video_bridge_begin(ctypes.c_void_p(self._bridge)):
            self._fail(f"zero-copy GL context unusable ({last_error(bridge)})")
            return False
        try:
            self._proc_resolver = mpv.MpvGlGetProcAddressFn(
                lambda _ctx, name: _macos_gl_symbol(name)
            )
            self._ctx = mpv.MpvRenderContext(
                self._handle,
                "opengl",
                opengl_init_params={"get_proc_address": self._proc_resolver},
            )
            self._ctx.update_cb = self.scheduleUpdate
            _log.info("mpv render context created on the zero-copy bridge")
        except Exception:
            _log.exception("mpv render context creation failed")
            self._failed = True
            return False
        finally:
            bridge.gv_video_bridge_end(ctypes.c_void_p(self._bridge))
        return True

    def _render_frame(self, bridge: Any) -> bool:
        if self._bridge is None or self._ctx is None:
            return False
        if not bridge.gv_video_bridge_begin(ctypes.c_void_p(self._bridge)):
            return False
        try:
            fbo = bridge.gv_video_bridge_fbo(ctypes.c_void_p(self._bridge))
            if not fbo:
                return False
            width, height = self._size
            # Without internal_format mpv assumes an 8-bit target and dithers
            # 10-bit video down to it -- which is exactly the precision the
            # surface exists to preserve.
            internal_format = bridge.gv_video_bridge_gl_internal_format(
                ctypes.c_void_p(self._bridge)
            )
            self._ctx.render(
                # No flip: the GL framebuffer's bottom-left origin and the way
                # Metal samples the IOSurface already agree. Asking mpv to flip
                # renders the picture upside down (verified against a grab).
                flip_y=False,
                opengl_fbo={
                    "fbo": int(fbo),
                    "w": width,
                    "h": height,
                    "internal_format": int(internal_format),
                },
                # Never sleep until the frame's presentation time: this is
                # Qt's render thread and the whole UI would wait with it.
                block_for_target_time=False,
            )
        except Exception:
            _log.exception("mpv render failed")
            self._failed = True
            return False
        finally:
            bridge.gv_video_bridge_end(ctypes.c_void_p(self._bridge))
        return True
