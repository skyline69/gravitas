"""In-scene video for the native engine.

Two paths, chosen per window:

* **Zero-copy** (the video bridge built, and the scene graph on Vulkan on
  Linux or on Metal on macOS): the engine renders on Qt's own device -- with
  libplacebo on Vulkan, with its own Metal renderer on macOS -- into an image
  the scene graph samples where it lies. Nothing crosses to the CPU. The Qt
  side of that -- the device, wrapping the image, teardown on the render
  thread -- is native_vk_bridge.py over native/linux/ and
  native_metal_bridge.py over native/macos/.
* **Readback** everywhere else: the engine renders the frame due now into
  this item's buffer (on a GPU device of its own, read back, or swscale), and
  Qt uploads it as a texture on whatever RHI it runs -- the same trade
  mpv_sw_item.py makes, with the same geometry rules. Zero-copy falls back to
  this at its first failure.

The engine does not push frames. The item asks for one on every scene-graph
frame while the engine wants_frames(), and the engine answers with the frame
due at its clock, dropping late ones -- so presentation runs at the display's
rate and never waits on the decoder.
"""

from __future__ import annotations

import ctypes
import logging
import weakref
from typing import Any, Protocol

from PySide6.QtCore import Property, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickItem, QQuickWindow, QSGNode, QSGSimpleTextureNode, QSGTexture
from shiboken6 import getCppPointer, wrapInstance

from gravitas.presentation.video import native_metal_bridge, native_vk_bridge
from gravitas.presentation.video.geometry import RESIZE_SETTLE_MS, fitted_rect, video_geometry

_log = logging.getLogger(__name__)

# The engine writes R, G, B and a padding byte per pixel, which is exactly
# QImage's RGBX8888 on either endianness.
_QIMAGE_FORMAT = QImage.Format.Format_RGBX8888

# How often an idle item checks whether the engine has started wanting
# frames again (a resume, a seek while paused). While frames flow, requests
# follow the display's own frame rate instead.
_KICK_INTERVAL_MS = 50


class _Bridge(Protocol):
    """One window's zero-copy bridge, whichever graphics API it speaks."""

    engine: int

    def attach(self, source: Any) -> None: ...
    def texture(self, image: int, width: int, height: int) -> int: ...


class _Backend(Protocol):
    """A zero-copy bridge module (native_vk_bridge, native_metal_bridge)."""

    def library(self) -> object | None: ...
    def last_error(self) -> str: ...
    def for_window(self, window_pointer: int) -> _Bridge | None: ...


class FrameSource(Protocol):
    """What the item needs of the engine's player."""

    def video_size(self) -> tuple[int, int] | None: ...
    def wants_frames(self) -> bool: ...
    def render(self, buffer: memoryview, width: int, height: int, stride: int) -> bool: ...


class NativeVideoItem(QQuickItem):
    """Same contract as the mpv items: set `handle`, get video."""

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._source: FrameSource | None = None
        self._buffer: ctypes.Array[ctypes.c_ubyte] | None = None
        self._view: memoryview | None = None
        self._image: QImage | None = None
        self._buffer_size = (0, 0)
        self._failed = False
        # None until the first frame decides; False once zero-copy failed.
        self._zero_copy: bool | None = None
        # The zero-copy bridge module for the window's graphics API.
        self._backend: _Backend | None = None
        # The image last shown on the zero-copy path: (image, width, height).
        self._shared: tuple[int, int, int] | None = None
        # Written on the GUI thread, read on the render thread; a stale read
        # costs one frame and nothing else.
        self._resizing = False
        self._resize_settle = QTimer(self)
        self._resize_settle.setSingleShot(True)
        self._resize_settle.setInterval(RESIZE_SETTLE_MS)
        self._resize_settle.timeout.connect(self._resize_finished)
        self._kick = QTimer(self)
        self._kick.setInterval(_KICK_INTERVAL_MS)
        self._kick.timeout.connect(self._request_frame)
        # Weak: the window owns this item, so a strong reference here would
        # let the item's wrapper be the last thing keeping the window alive --
        # and freeing it would then destroy the window, and with it this item,
        # mid-deallocation (a segfault in windowChanged, seen in the tests).
        self._window: weakref.ref[QQuickWindow] | None = None
        self.windowChanged.connect(self._attach_window)

    def _get_handle(self) -> Any:
        return self._source

    def _set_handle(self, handle: Any) -> None:
        self._source = handle
        self._failed = False
        self._shared = None
        if handle is None:
            self._kick.stop()
        else:
            self._kick.start()
        self.update()

    handle = Property("QVariant", _get_handle, _set_handle)  # type: ignore[arg-type]

    def _attach_window(self, window: QQuickWindow | None) -> None:
        previous = self._window() if self._window is not None else None
        if previous is not None:
            previous.frameSwapped.disconnect(self._request_frame)
        self._window = weakref.ref(window) if window is not None else None
        if window is not None:
            # frameSwapped is emitted on the render thread; the queued
            # connection brings it to this item's thread.
            window.frameSwapped.connect(self._request_frame, Qt.ConnectionType.QueuedConnection)

    def _request_frame(self) -> None:
        source = self._source
        if source is None or self._failed:
            return
        try:
            wanted = source.wants_frames()
        except Exception:  # the engine was shut down under us
            wanted = False
        if wanted:
            self.update()

    def geometryChange(self, new_geometry: Any, old_geometry: Any) -> None:
        """Keep the frame on screen and only move it while a resize is in
        progress; see MpvSwVideoItem.geometryChange for the measurements."""
        if new_geometry.size() != old_geometry.size():
            self._resizing = True
            self._resize_settle.start()
        super().geometryChange(new_geometry, old_geometry)

    def _resize_finished(self) -> None:
        self._resizing = False
        self.update()

    def _ensure_buffer(self, width: int, height: int) -> None:
        if self._buffer is not None and (width, height) == self._buffer_size:
            return
        self._buffer_size = (width, height)
        self._buffer = (ctypes.c_ubyte * (width * height * 4))()
        # The engine writes through a byte view of the buffer, and the QImage
        # is a view of the same memory: no copy on this side of the upload.
        self._view = memoryview(self._buffer).cast("B")
        self._image = QImage(  # type: ignore[call-overload]
            self._buffer, width, height, width * 4, _QIMAGE_FORMAT
        )

    def updatePaintNode(  # type: ignore[override]  # C++ returns a nullable pointer
        self, node: QSGNode | None, _data: Any
    ) -> QSGNode | None:
        window = self.window()
        source = self._source
        if window is None or source is None or self._failed:
            return node
        try:
            size = source.video_size()
        except Exception:
            return node
        video_w, video_h = size or (0, 0)
        if not video_w or not video_h:
            return node
        if self._zero_copy is not False:
            painted = self._paint_shared(window, source, node, video_w, video_h)
            if painted is not _FALL_BACK:
                return painted  # type: ignore[no-any-return]
            # A node made for the other path may own its texture; start over.
            node = None
        rect, buffer_w, buffer_h = video_geometry(
            self.width(), self.height(), window.devicePixelRatio(), video_w, video_h
        )
        if self._resizing and isinstance(node, QSGSimpleTextureNode):
            node.setRect(rect)
            return node
        self._ensure_buffer(buffer_w, buffer_h)
        assert self._view is not None and self._image is not None
        try:
            drawn = source.render(self._view, buffer_w, buffer_h, buffer_w * 4)
        except Exception:
            # A black item is the acceptable degraded mode; an exception
            # escaping updatePaintNode is not.
            _log.exception("native video render failed; video disabled")
            self._failed = True
            return node
        if not drawn:
            # Nothing new is due: keep the texture, follow the item.
            if isinstance(node, QSGSimpleTextureNode):
                node.setRect(rect)
            return node
        texture = window.createTextureFromImage(
            self._image, QQuickWindow.CreateTextureOption.TextureIsOpaque
        )
        texture_node = node if isinstance(node, QSGSimpleTextureNode) else QSGSimpleTextureNode()
        texture_node.setOwnsTexture(True)
        texture_node.setTexture(texture)
        texture_node.setRect(rect)
        texture_node.setFiltering(QSGTexture.Filtering.Linear)
        return texture_node

    def _paint_shared(
        self, window: QQuickWindow, source: Any, node: QSGNode | None, video_w: int, video_h: int
    ) -> Any:
        """The zero-copy path, or _FALL_BACK to use the readback one."""
        bridge = self._attach(window, source)
        if bridge is None:
            return _FALL_BACK
        try:
            # A new item (or one handed a new engine) has no image yet, and
            # the engine answers "nothing new" when nothing is due.
            rendered = source.render_shared(self._shared is None)
        except Exception as exc:
            _log.warning("zero-copy video failed (%s); copying frames instead", exc)
            self._zero_copy = False
            return _FALL_BACK
        if rendered is not None:
            self._shared = (int(rendered[0]), int(rendered[1]), int(rendered[2]))
        if self._shared is None:
            return node  # nothing decoded yet
        image, width, height = self._shared
        rect = fitted_rect(self.width(), self.height(), video_w, video_h)
        if rendered is None and isinstance(node, QSGSimpleTextureNode):
            node.setRect(rect)
            return node
        address = bridge.texture(image, width, height)
        if not address:
            _log.warning(
                "zero-copy texture failed (%s); copying frames instead",
                self._backend.last_error() if self._backend else "",
            )
            self._zero_copy = False
            return _FALL_BACK
        texture_node = node if isinstance(node, QSGSimpleTextureNode) else QSGSimpleTextureNode()
        # The bridge owns the texture and keeps it until the scene graph goes.
        texture_node.setOwnsTexture(False)
        texture_node.setTexture(wrapInstance(address, QSGTexture))  # type: ignore[arg-type]
        texture_node.setRect(rect)
        texture_node.setFiltering(QSGTexture.Filtering.Linear)
        return texture_node

    def _attach(self, window: QQuickWindow, source: Any) -> _Bridge | None:
        """The window's bridge with this engine attached, or None (and the
        readback path from now on) when zero-copy is not available here."""
        if self._zero_copy is None:
            renderer = window.rendererInterface()
            if renderer is None:
                return None  # no scene graph yet: decide on a later frame
            api = renderer.graphicsApi()
            backend: _Backend | None = None
            if api == renderer.GraphicsApi.Vulkan and hasattr(source, "attach_vulkan"):
                backend = native_vk_bridge
            elif api == renderer.GraphicsApi.Metal and hasattr(source, "attach_metal"):
                backend = native_metal_bridge
            self._backend = backend if backend and backend.library() is not None else None
            self._zero_copy = self._backend is not None
            if not self._zero_copy:
                return None
        assert self._backend is not None
        bridge = self._backend.for_window(getCppPointer(window)[0])
        if bridge is None:
            self._zero_copy = False
            return None
        if bridge.engine != id(source):
            try:
                bridge.attach(source)
            except Exception as exc:
                _log.warning("zero-copy video unavailable (%s); copying frames instead", exc)
                self._zero_copy = False
                return None
            bridge.engine = id(source)
            self._shared = None
        return bridge


# Returned by _paint_shared to hand the frame to the readback path.
_FALL_BACK = object()
