"""In-scene mpv video item: libmpv render API -> QQuickFramebufferObject.

Foreign-window (`wid`) embedding does not exist on Wayland, so mpv draws into
an FBO inside the Qt Quick scene instead. The mpv handle is injected by
PlayerController.attachVideo(); until then the item renders nothing.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, QMetaObject, QSize, Qt, Slot
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import QOpenGLFramebufferObject
from PySide6.QtQuick import QQuickFramebufferObject, QQuickItem


class _Renderer(QQuickFramebufferObject.Renderer):
    def __init__(self, item: MpvVideoItem) -> None:
        super().__init__()
        self._item = item
        self._ctx: Any = None
        self._get_proc: Any = None  # keep the ctypes callback alive

    def createFramebufferObject(self, size: QSize) -> QOpenGLFramebufferObject:
        # First call happens on the render thread with the GL context current —
        # the only safe place to create the mpv render context.
        if self._ctx is None and self._item.handle is not None:
            import mpv  # type: ignore[import-untyped]

            def get_proc_address(_ctx: Any, name: bytes) -> int:
                glctx = QOpenGLContext.currentContext()
                if glctx is None:
                    return 0
                addr = glctx.getProcAddress(name)
                return int(addr) if addr else 0

            self._get_proc = mpv.MpvGlGetProcAddressFn(get_proc_address)
            self._ctx = mpv.MpvRenderContext(
                self._item.handle,
                "opengl",
                opengl_init_params={"get_proc_address": self._get_proc},
            )
            # Fires on mpv's thread whenever a new frame is ready; hop to the
            # GUI thread to schedule a repaint.
            self._ctx.update_cb = self._item.scheduleUpdate
        return super().createFramebufferObject(size)

    def render(self) -> None:
        if self._ctx is None:
            return
        fbo = self.framebufferObject()
        self._ctx.render(
            flip_y=False,
            opengl_fbo={"fbo": int(fbo.handle()), "w": fbo.width(), "h": fbo.height()},
        )


class MpvVideoItem(QQuickFramebufferObject):
    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self._handle: Any = None
        # mpv renders bottom-up; mirror instead of flipping inside mpv so the
        # FBO contents and Qt's sampling agree.
        self.setMirrorVertically(True)

    def _get_handle(self) -> Any:
        return self._handle

    def _set_handle(self, handle: Any) -> None:
        self._handle = handle
        self.update()

    handle = Property("QVariant", _get_handle, _set_handle)  # type: ignore[arg-type]

    def scheduleUpdate(self) -> None:
        # Called from mpv's render thread — queue onto the GUI thread.
        QMetaObject.invokeMethod(self, "doUpdate", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def doUpdate(self) -> None:
        self.update()

    def createRenderer(self) -> QQuickFramebufferObject.Renderer:
        return _Renderer(self)
