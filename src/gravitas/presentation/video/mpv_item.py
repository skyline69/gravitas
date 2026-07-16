"""In-scene mpv video item: libmpv render API -> QQuickFramebufferObject.

Foreign-window (`wid`) embedding does not exist on Wayland, so mpv draws into
an FBO inside the Qt Quick scene instead. The mpv handle is injected by
PlayerController.attachVideo(); until then the item renders nothing.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
import weakref
from typing import Any

from PySide6.QtCore import Property, QMetaObject, QObject, QSize, Qt, Slot
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject
from PySide6.QtQuick import QQuickFramebufferObject, QQuickItem
from shiboken6 import isValid

_log = logging.getLogger(__name__)

_MACOS_OPENGL_FRAMEWORK = "/System/Library/Frameworks/OpenGL.framework/OpenGL"
_macos_gl: ctypes.CDLL | None = None


def _macos_gl_symbol(name: bytes) -> int:
    """Resolve a GL symbol from the OpenGL framework via dlsym."""
    global _macos_gl
    try:
        if _macos_gl is None:
            _macos_gl = ctypes.CDLL(_MACOS_OPENGL_FRAMEWORK)
        fn = getattr(_macos_gl, name.decode("ascii"))
        return ctypes.cast(fn, ctypes.c_void_p).value or 0
    except (OSError, AttributeError, UnicodeDecodeError):
        return 0


class _UpdateBridge(QObject):
    """Receives mpv's frame callback on behalf of the item.

    The callback outlives the item: QML deletes MpvVideoItem's C++ object when
    the player page is popped, while the mpv core lives on in PlayerController
    (the page only stops playback), so mpv keeps signalling new frames until
    the renderer is freed on the render thread some frames later. Posting to
    the item directly raised on every one of those calls.

    This object is owned by Python, not by the QML engine, so it stays valid
    across the item's death. It holds the item weakly and re-checks it in
    _fire(), which runs on the GUI thread -- the same thread QML deletes items
    on, so the item cannot go away between the check and update().
    """

    def __init__(self, item: MpvVideoItem) -> None:
        # Deliberately parentless: a parented QObject would be deleted along
        # with the item, which is the very thing this must outlive. Constructed
        # on the GUI thread, so queued calls land there too.
        super().__init__()
        self._item = weakref.ref(item)

    def schedule(self) -> None:
        # Called from mpv's render thread. Touches nothing but this object.
        QMetaObject.invokeMethod(self, "_fire", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _fire(self) -> None:
        item = self._item()
        if item is None or not isValid(item):
            return
        item.update()


class _Renderer(QQuickFramebufferObject.Renderer):
    def __init__(self, item: MpvVideoItem) -> None:
        super().__init__()
        self._item = item
        self._ctx: Any = None
        self._get_proc: Any = None  # keep the ctypes callback alive

    def createFramebufferObject(self, size: QSize) -> QOpenGLFramebufferObject:
        # First call happens on the render thread with the GL context current —
        # the only safe place to create the mpv render context. Any failure
        # must be swallowed: an exception escaping this override hands Qt a
        # null FBO and the process dies with SIGSEGV; a black video item is
        # the acceptable degraded mode.
        if self._ctx is None and self._item.handle is not None:
            try:
                self._create_context()
            except Exception:
                _log.exception("mpv render context creation failed; video disabled")
        return super().createFramebufferObject(size)

    def _create_context(self) -> None:
        import mpv  # type: ignore[import-untyped]

        def get_proc_address(_ctx: Any, name: bytes) -> int:
            glctx = QOpenGLContext.currentContext()
            if glctx is None:
                return 0
            addr = glctx.getProcAddress(name)
            if addr:
                return int(addr)
            # macOS/CGL: Qt occasionally returns null for core GL symbols;
            # resolve them straight from the OpenGL framework instead.
            if sys.platform == "darwin":
                return _macos_gl_symbol(name)
            return 0

        self._get_proc = mpv.MpvGlGetProcAddressFn(get_proc_address)
        self._ctx = mpv.MpvRenderContext(
            self._item.handle,
            "opengl",
            opengl_init_params={"get_proc_address": self._get_proc},
        )
        # Fires on mpv's thread whenever a new frame is ready; hop to the
        # GUI thread to schedule a repaint.
        self._ctx.update_cb = self._item.scheduleUpdate

    def __del__(self) -> None:
        # Runs on the render thread with the GL context current, which is what
        # freeing the mpv render context requires. That is not luck, and it is
        # load-bearing: Qt deletes the Renderer from ~QSGFramebufferObjectNode
        # there, and since createRenderer() hands this object to C++ and keeps
        # no Python reference, shiboken holds the last one and drops it inside
        # that delete -- so __del__ runs synchronously on that same thread.
        #
        # Never keep a Python reference to a _Renderer (e.g. stashing it on the
        # item). PySide does NOT run __del__ when C++ deletes the object; it
        # only invalidates the wrapper. One stray reference defers this to
        # whatever thread later drops it, with no GL context current.
        #
        # Free the render context before the mpv core can go away; leaking it
        # segfaults inside libmpv at teardown.
        ctx, self._ctx = self._ctx, None
        if ctx is not None:
            with contextlib.suppress(Exception):
                # Detach first: mpv_render_context_set_update_callback is
                # safe to call at any time, and passing None installs a no-op,
                # so no callback can be in flight while free() runs.
                ctx.update_cb = None
            with contextlib.suppress(Exception):
                ctx.free()

    def render(self) -> None:
        if self._ctx is None:
            return
        fbo = self.framebufferObject()
        # block_for_target_time=False: never let mpv stall Qt's render thread
        # waiting for the frame's presentation time — that wait shows up as
        # UI-wide lag while video plays.
        self._ctx.render(
            flip_y=False,
            opengl_fbo={"fbo": int(fbo.handle()), "w": fbo.width(), "h": fbo.height()},
            block_for_target_time=False,
        )


class MpvVideoItem(QQuickFramebufferObject):
    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self._handle: Any = None
        self._bridge = _UpdateBridge(self)
        # GL FBO origin (bottom-left) already matches mpv's output here —
        # adding mirrorVertically or flip_y on top shows the video upside
        # down. If a platform ever disagrees, toggle exactly ONE of the two.

    def _get_handle(self) -> Any:
        return self._handle

    def _set_handle(self, handle: Any) -> None:
        self._handle = handle
        self.update()

    handle = Property("QVariant", _get_handle, _set_handle)  # type: ignore[arg-type]

    def scheduleUpdate(self) -> None:
        # Called from mpv's render thread, possibly after this item's C++
        # object is gone — so it must stay pure Python and only reach for the
        # bridge, which survives that.
        self._bridge.schedule()

    def createRenderer(self) -> QQuickFramebufferObject.Renderer:
        return _Renderer(self)
