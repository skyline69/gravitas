"""Zero-copy video item for macOS: mpv renders on the GPU, Qt draws the result.

mpv draws through OpenGL into an IOSurface that Metal samples directly (see
native/macos/), so a decoded frame goes GPU to GPU with nothing crossing the
CPU. That is the difference from mpv_sw_item.py, the fallback used when the
native bridge is missing or was built against a different Qt: there every
frame is converted and uploaded by hand.

NOTHING HERE BELONGS TO THE ITEM. The GL context, mpv's render context and
the surface all live in a _Session held per window, because the two lifetimes
they have to match are longer than an item's:

  * The mpv core outlives the player page -- PlayerController owns it for the
    whole session and a page pop only stops playback. libmpv allows exactly
    one render context per core, so creating one per item makes the SECOND
    playback fail with "There is already a mpv_render_context set" and render
    nothing.
  * Qt's batch renderer keeps using a texture for an unbounded number of
    frames after the node referencing it is gone, so a surface can only be
    freed once the scene graph is invalidated -- which is also the only
    moment the session is torn down.

The order of operations inside a session is forced, and it is not the
obvious one:

  1. The bridge, and its GL context, is created as soon as there is a window
     and an mpv handle -- BEFORE anything is known about the video.
  2. mpv's render context is created on that GL context immediately after. It
     has to exist before mpv can bring up its video output at all, and until
     it does mpv cannot report a resolution.
  3. Only then does a frame arrive, and with it the video's size, which the
     bridge turns into a surface.

Everything runs on the render thread: inside updatePaintNode, or a
directly-connected sceneGraphInvalidated handler.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import weakref
from typing import Any

from PySide6.QtCore import Property, QRectF
from PySide6.QtGui import QGuiApplication
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


class _Session:
    """The GPU plumbing for one window: bridge, mpv render context, surface.

    Outlives every item that draws through it. Torn down only when the window's
    scene graph goes away.
    """

    def __init__(self, bridge_handle: int, mpv_handle: Any) -> None:
        self.bridge = bridge_handle
        self.mpv_handle = mpv_handle
        self.ctx: Any = None
        self.size: tuple[int, int] = (0, 0)
        # mpv calls these for as long as its render context lives, so the
        # ctypes trampolines have to outlive the call that installs them.
        self.proc_resolver: Any = None
        self._owner: weakref.ReferenceType[Any] | None = None
        # Set when the scene graph is about to stop. Rendering one more frame
        # into a scene that is being dismantled is what crashes.
        self.stopped = False

    def set_owner(self, item: Any) -> None:
        """Point frame notifications at the item currently on screen.

        Plain attribute assignment, deliberately: re-installing the mpv
        callback instead would build a new ctypes closure and drop the old one
        while mpv's VO thread may be inside it -- a use-after-free that
        crashed a second into playback.
        """
        self._owner = weakref.ref(item)

    def silence(self) -> None:
        """Drop the owner so nothing is woken again. Safe from any thread: it
        replaces a reference, it does not free anything."""
        self._owner = None

    def notify(self) -> None:
        """mpv's VO thread: wake whichever item is drawing this session."""
        reference = self._owner
        item = reference() if reference is not None else None
        if item is not None:
            item.scheduleUpdate()

    def free_render_context(self, bridge: Any) -> None:
        """Hand mpv's render context back. Render thread only.

        libmpv requires ITS GL context to be current for this, the same as for
        rendering -- freeing without it hangs waiting on a context that is not
        there.
        """
        ctx, self.ctx = self.ctx, None
        if ctx is None:
            return
        self.silence()
        current = bool(bridge.gv_video_bridge_begin(ctypes.c_void_p(self.bridge)))
        try:
            with contextlib.suppress(Exception):
                # Detach first: no callback can be in flight while free() runs.
                ctx.update_cb = None
            with contextlib.suppress(Exception):
                ctx.free()
        finally:
            if current:
                bridge.gv_video_bridge_end(ctypes.c_void_p(self.bridge))

    def destroy(self, bridge: Any) -> None:
        self.free_render_context(bridge)
        handle, self.bridge = self.bridge, 0
        if handle:
            bridge.gv_video_bridge_destroy(ctypes.c_void_p(handle))


# Keyed by QQuickWindow pointer. One entry in practice -- the app has a single
# window and plays one thing at a time -- but keyed rather than global so a
# second window cannot silently adopt the first one's GL context.
_SESSIONS: dict[int, _Session] = {}


class MpvMetalVideoItem(QQuickItem):
    """Same contract as the other two video items: set `handle`, get video."""

    def __init__(self, parent: QQuickItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._handle: Any = None
        self._failed = False
        self._connected_window: QQuickWindow | None = None
        self._update_bridge = _UpdateBridge(self)

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
        """Arrange for playback to stop at quit -- and nothing more.

        There is deliberately no scene-graph teardown hook here, and it cost
        several attempts to learn why. The scene-graph signals are emitted on
        the RENDER thread, so a Python slot on them has to take the GIL; at
        shutdown the GUI thread is inside Qt holding the GIL and waiting for
        the render thread to finish. The render thread then blocks in
        PyGILState_Ensure and neither side moves again -- a hang, reproduced
        eight times out of eight and confirmed by sampling the stacks.

        So nothing is freed on the way out. The session's GL context,
        IOSurface and mpv render context are handed back to the operating
        system with the process, which costs nothing at that point. What DOES
        matter is that mpv stops being asked for frames while Qt dismantles
        the scene, and that is a plain flag set from the GUI thread.
        """
        if self._connected_window is window:
            return
        self._connected_window = window
        app = QGuiApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop)

    def _stop(self) -> None:
        """Quitting: ask mpv for nothing more.

        GUI thread, and it touches no GL, mpv or scene-graph resource -- it
        sets two flags. mpv only draws when asked, so not asking is enough to
        keep the teardown quiet.
        """
        window = self._connected_window
        if window is None:
            return
        session = _SESSIONS.get(getCppPointer(window)[0])
        if session is not None:
            session.stopped = True
            session.silence()

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
        session = self._session(bridge, window)
        if session is None or not self._ensure_render_context(bridge, session):
            return node

        video_w, video_h = self._video_size()
        if not video_w or not video_h:
            # mpv has not decoded anything yet. Keep whatever is on screen
            # rather than flashing a black frame.
            return node
        if (video_w, video_h) != session.size:
            if not bridge.gv_video_bridge_set_size(
                ctypes.c_void_p(session.bridge), video_w, video_h
            ):
                self._fail(f"zero-copy surface failed ({last_error(bridge)})")
                return node
            session.size = (video_w, video_h)
            pixel_format = (
                bridge.gv_video_bridge_format(ctypes.c_void_p(session.bridge)) or b""
            ).decode()
            _log.info("zero-copy video surface at %dx%d, %s", video_w, video_h, pixel_format)

        if not self._render_frame(bridge, session):
            return node
        address = bridge.gv_video_bridge_texture(ctypes.c_void_p(session.bridge))
        if not address:
            return node

        texture_node = node if isinstance(node, QSGSimpleTextureNode) else QSGSimpleTextureNode()
        # The session owns the texture and outlives the node; letting the node
        # own it would free it out from under the renderer.
        texture_node.setOwnsTexture(False)
        # wrapInstance hands back a shiboken Object; it IS the bridge's
        # QSGTexture, but the stubs cannot know that.
        texture_node.setTexture(wrapInstance(address, QSGTexture))  # type: ignore[arg-type]
        texture_node.setRect(fitted_rect(self.width(), self.height(), video_w, video_h))
        texture_node.setFiltering(QSGTexture.Filtering.Linear)
        return texture_node

    def _session(self, bridge: Any, window: QQuickWindow) -> _Session | None:
        """This window's session, created on first use and reused after that.

        Reuse is the point: a fresh render context per item is what breaks the
        second playback, since libmpv allows only one per core.
        """
        key = getCppPointer(window)[0]
        session = _SESSIONS.get(key)
        if session is not None:
            if session.mpv_handle is self._handle:
                return session
            # A different mpv core (the player was rebuilt): its render context
            # belongs to the old one and cannot be reused.
            session.free_render_context(bridge)
            session.mpv_handle = self._handle
            return session
        handle = bridge.gv_video_bridge_create(key)
        if not handle:
            self._fail(f"zero-copy bridge unavailable ({last_error(bridge)})")
            return None
        session = _Session(handle, self._handle)
        _SESSIONS[key] = session
        return session

    def _ensure_render_context(self, bridge: Any, session: _Session) -> bool:
        session.set_owner(self)
        if session.ctx is not None:
            return True
        import mpv  # type: ignore[import-untyped]

        if not bridge.gv_video_bridge_begin(ctypes.c_void_p(session.bridge)):
            self._fail(f"zero-copy GL context unusable ({last_error(bridge)})")
            return False
        try:
            session.proc_resolver = mpv.MpvGlGetProcAddressFn(
                lambda _ctx, name: _macos_gl_symbol(name)
            )
            session.ctx = mpv.MpvRenderContext(
                session.mpv_handle,
                "opengl",
                opengl_init_params={"get_proc_address": session.proc_resolver},
            )
            # Installed ONCE, and never replaced: the session dispatches to
            # whichever item currently owns it.
            session.ctx.update_cb = session.notify
            _log.info("mpv render context created on the zero-copy bridge")
        except Exception:
            _log.exception("mpv render context creation failed")
            self._failed = True
            return False
        finally:
            bridge.gv_video_bridge_end(ctypes.c_void_p(session.bridge))
        return True

    def _render_frame(self, bridge: Any, session: _Session) -> bool:
        if session.ctx is None or session.stopped:
            return False
        if not bridge.gv_video_bridge_begin(ctypes.c_void_p(session.bridge)):
            return False
        try:
            fbo = bridge.gv_video_bridge_fbo(ctypes.c_void_p(session.bridge))
            if not fbo:
                return False
            width, height = session.size
            # Without internal_format mpv assumes an 8-bit target and dithers
            # 10-bit video down to it -- which is exactly the precision the
            # surface exists to preserve.
            internal_format = bridge.gv_video_bridge_gl_internal_format(
                ctypes.c_void_p(session.bridge)
            )
            session.ctx.render(
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
            bridge.gv_video_bridge_end(ctypes.c_void_p(session.bridge))
        return True
