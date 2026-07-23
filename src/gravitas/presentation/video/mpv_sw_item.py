"""In-scene mpv video item for non-OpenGL scene graphs: libmpv's software
render API -> QImage -> QSGSimpleTextureNode.

The OpenGL item next door (`mpv_item.py`) is the faster of the two -- mpv
renders straight into a scene-graph FBO, zero copies -- and is what Linux and
Windows use. It only works while Qt Quick is on the OpenGL RHI.

macOS runs on Metal instead, and this item is what pays for that. The reason
is the render loop: Qt refuses its threaded one on macOS with OpenGL (forcing
it crashes inside -[NSOpenGLContext setView:] on the render thread), so an
OpenGL scene graph there produces every animation frame on the same thread
that runs Python, QML incubation and texture uploads. Metal buys the threaded
loop and a visibly smoother UI; libmpv's render API speaks OpenGL and nothing
else, so the FBO item has nothing to draw into.

libmpv's SOFTWARE render API is the way across: mpv renders a frame into a
plain CPU buffer, which Qt uploads as a texture on whatever RHI it happens to
be using. It costs a per-frame conversion and upload the OpenGL path does not
(measured on an M2: ~3.6 ms per 1080p frame, ~6.8 ms per 4K frame, on the
render thread), which is the price of the smoother UI around it.
See infrastructure/graphics.py for where that trade is decided.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import threading
from typing import Any

from PySide6.QtCore import Property, QRectF
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickItem, QQuickWindow, QSGNode, QSGSimpleTextureNode, QSGTexture

from gravitas.presentation.video.mpv_item import _UpdateBridge

_log = logging.getLogger(__name__)

# python-mpv's render-param table stops at 16 (the DRM params); the four
# software-render params are 17..20 in libmpv's render.h and it never learned
# them. Registering them here is what makes MpvRenderContext.render() accept
# sw_size/sw_format/sw_stride/sw_pointer.
#
# All three pointer-shaped ones go in as c_void_p: python-mpv passes that
# straight through as the param payload, which is exactly what libmpv wants
# (an int[2], a size_t* and the pixel buffer). The caller owns keeping the
# ctypes objects behind those pointers alive across the call.
_SW_RENDER_PARAMS = {
    "sw_size": (17, ctypes.c_void_p),
    "sw_format": (18, str),
    "sw_stride": (19, ctypes.c_void_p),
    "sw_pointer": (20, ctypes.c_void_p),
}

# mpv writes R,G,B,X bytes in memory for "rgb0"; QImage's RGBX8888 reads the
# same order on either endianness. Pairing "rgb0" with Format_RGB32 instead
# swaps red and blue on a little-endian machine.
_SW_FORMAT = "rgb0"
_QIMAGE_FORMAT = QImage.Format.Format_RGBX8888


def video_geometry(
    item_width: float,
    item_height: float,
    dpr: float,
    video_width: int,
    video_height: int,
) -> tuple[QRectF, int, int]:
    """Where the video sits inside the item, and how big a buffer to ask mpv for.

    Returns (rect in item coordinates, buffer width, buffer height in device
    pixels). The rect letterboxes the video's aspect inside the item -- the
    OpenGL path has mpv do that inside the FBO, but here every rendered pixel
    is CPU work, so the bars are better left undrawn and the node simply made
    smaller than the item.

    The buffer never exceeds the video's own resolution: upscaling is work the
    GPU does for free when the texture is stretched over a larger rect, and
    paying for it on the CPU buys nothing. It is capped to the item as well,
    so a 4K file in a small window renders small.
    """
    if item_width <= 0 or item_height <= 0 or video_width <= 0 or video_height <= 0:
        return QRectF(0, 0, max(item_width, 0.0), max(item_height, 0.0)), 1, 1
    scale = min(item_width / video_width, item_height / video_height)
    fitted_w = video_width * scale
    fitted_h = video_height * scale
    rect = QRectF(
        (item_width - fitted_w) / 2.0,
        (item_height - fitted_h) / 2.0,
        fitted_w,
        fitted_h,
    )
    buffer_w = max(1, min(video_width, round(fitted_w * dpr)))
    buffer_h = max(1, min(video_height, round(fitted_h * dpr)))
    return rect, buffer_w, buffer_h


class MpvSwVideoItem(QQuickItem):
    """Same contract as MpvVideoItem: set `handle`, get video.

    PlayerController.attachVideo() only ever writes that one property, so
    main.py can register either item under the QML name `MpvVideo` and
    Player.qml never learns which one it got.
    """

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._handle: Any = None
        self._ctx: Any = None
        self._buffer: ctypes.Array[ctypes.c_char] | None = None
        self._image: QImage | None = None
        self._buffer_size = (0, 0)
        # Serializes render against free: the render happens on the render
        # thread, the free on the GUI thread at teardown, and libmpv will
        # crash if they overlap.
        self._lock = threading.Lock()
        self._bridge = _UpdateBridge(self)
        # The QML engine deletes this item on the GUI thread whenever the
        # player page pops, but the mpv core outlives it (PlayerController
        # owns it), so the render context has to be freed explicitly. PySide
        # does not run __del__ for C++-owned objects, hence a hook that is not
        # tied to this object's lifetime.
        app = QGuiApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._free_context)

    def _get_handle(self) -> Any:
        return self._handle

    def _set_handle(self, handle: Any) -> None:
        self._handle = handle
        self.update()

    handle = Property("QVariant", _get_handle, _set_handle)  # type: ignore[arg-type]

    def scheduleUpdate(self) -> None:
        # Runs on mpv's thread. Same rule as the OpenGL item: touch nothing
        # but the bridge, which survives this item's death.
        self._bridge.schedule()

    def itemChange(self, change: QQuickItem.ItemChange, value: Any) -> Any:
        # Leaving the scene (the player page popping) is the deterministic
        # moment to hand the render context back, and it runs on the GUI
        # thread where no render can start behind it.
        if change == QQuickItem.ItemChange.ItemSceneChange and value.window() is None:
            self._free_context()
        # Protected in C++ and exposed by PySide, but absent from the stubs.
        return super().itemChange(change, value)  # type: ignore[misc]

    def _ensure_context(self) -> bool:
        if self._ctx is not None:
            return True
        if self._handle is None:
            return False
        try:
            import mpv  # type: ignore[import-untyped]

            mpv.MpvRenderParam.TYPES.update(_SW_RENDER_PARAMS)
            self._ctx = mpv.MpvRenderContext(self._handle, "sw")
            self._ctx.update_cb = self.scheduleUpdate
            _log.info("mpv software render context created (scene graph is not OpenGL)")
        except Exception:
            # A black video item is the acceptable degraded mode; an exception
            # escaping updatePaintNode is not.
            _log.exception("mpv software render context creation failed; video disabled")
            return False
        return True

    def _free_context(self) -> None:
        with self._lock:
            ctx, self._ctx = self._ctx, None
        if ctx is None:
            return
        with contextlib.suppress(Exception):
            # Detach first so no callback can be in flight while free() runs.
            ctx.update_cb = None
        with contextlib.suppress(Exception):
            ctx.free()

    def _ensure_buffer(self, width: int, height: int) -> None:
        if self._buffer is not None and (width, height) == self._buffer_size:
            return
        self._buffer_size = (width, height)
        self._buffer = (ctypes.c_char * (width * height * 4))()
        # The QImage is a VIEW on that buffer -- no copy per frame. It must be
        # rebuilt whenever the buffer is, or it points at freed memory.
        # The ctypes array is a writable buffer, which is the overload PySide
        # needs to WRAP it rather than copy; the stubs only know the bytes one.
        self._image = QImage(  # type: ignore[call-overload]
            self._buffer, width, height, width * 4, _QIMAGE_FORMAT
        )

    def _video_size(self) -> tuple[int, int]:
        """mpv's display size for the current file (aspect already applied)."""
        try:
            return int(self._handle.dwidth or 0), int(self._handle.dheight or 0)
        except Exception:  # no file loaded yet, or mpv is gone
            return 0, 0

    def updatePaintNode(  # type: ignore[override]  # C++ returns a nullable pointer
        self, node: QSGNode | None, _data: Any
    ) -> QSGNode | None:
        window = self.window()
        if window is None or not self._ensure_context():
            return node
        video_w, video_h = self._video_size()
        rect, buffer_w, buffer_h = video_geometry(
            self.width(), self.height(), window.devicePixelRatio(), video_w, video_h
        )
        if not video_w or not video_h:
            # Nothing decoded yet (or playback ended): keep whatever is on
            # screen rather than flashing a black frame.
            return node
        self._ensure_buffer(buffer_w, buffer_h)
        assert self._buffer is not None
        size = (ctypes.c_int * 2)(buffer_w, buffer_h)
        stride = ctypes.c_size_t(buffer_w * 4)
        with self._lock:
            if self._ctx is None:
                return node
            try:
                self._ctx.render(
                    sw_size=ctypes.cast(size, ctypes.c_void_p),
                    sw_format=_SW_FORMAT,
                    sw_stride=ctypes.cast(ctypes.pointer(stride), ctypes.c_void_p),
                    sw_pointer=ctypes.cast(self._buffer, ctypes.c_void_p),
                    # Same reasoning as the OpenGL path: never let mpv sleep
                    # until the frame's presentation time on Qt's thread.
                    # Without this the render call blocks for a whole frame
                    # interval (~29 ms measured) and the UI stalls with it.
                    block_for_target_time=False,
                )
            except Exception:
                _log.exception("mpv software render failed; video disabled")
                return node
        assert self._image is not None
        texture = window.createTextureFromImage(
            self._image, QQuickWindow.CreateTextureOption.TextureIsOpaque
        )
        texture_node = node if isinstance(node, QSGSimpleTextureNode) else QSGSimpleTextureNode()
        texture_node.setOwnsTexture(True)
        texture_node.setTexture(texture)
        texture_node.setRect(rect)
        texture_node.setFiltering(QSGTexture.Filtering.Linear)
        return texture_node
