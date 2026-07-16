"""Get a stremio:// URL from the desktop into this process.

Two platforms deliver a registered scheme two different ways, and neither
resembles the other:

- **Linux**: the browser executes `gravitas <url>`. That is a *new process*
  while one is very likely already running, so it must hand the URL to the
  running instance and get out of the way -- two Gravitas windows with separate
  SQLite and settings state is not a thing a user asked for.
- **macOS**: the OS does not re-exec anything. It posts a QFileOpenEvent to the
  already-running app, and at cold start delivers it once the event loop is up.

This module owns both, and everything above it sees one signal: a URL arrived.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtGui import QFileOpenEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_log = logging.getLogger(__name__)

# Enough that a second process fails fast rather than hanging the click, and
# generous enough to survive a busy machine.
_CONNECT_TIMEOUT_MS = 500
_WRITE_TIMEOUT_MS = 1000


def socket_name() -> str:
    """Per-user socket name.

    Scoped by uid because QLocalServer puts its socket in a shared /tmp on
    Linux: an unscoped name would let one user's link reach -- or simply block
    -- another user's Gravitas.
    """
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return f"gravitas-deeplink-{uid}"


def forward_to_running_instance(url: str, *, name: str | None = None) -> bool:
    """Hand `url` to an already-running Gravitas. True if one took it.

    False means no instance is listening and the caller is the primary. Called
    before any GUI exists, so it must never raise: a failure here has to end as
    "start normally", not as a crash on a link click.
    """
    socket = QLocalSocket()
    socket.connectToServer(name or socket_name())
    if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    try:
        socket.write(url.encode("utf-8"))
        if not socket.waitForBytesWritten(_WRITE_TIMEOUT_MS):
            _log.warning("connected to the running instance but could not send %s", url)
            return False
        return True
    finally:
        socket.disconnectFromServer()


class DeepLinkListener(QObject):
    """Emits linkReceived for every stremio:// URL this process is handed."""

    linkReceived = Signal(str)

    def __init__(self, parent: QObject | None = None, *, name: str | None = None) -> None:
        super().__init__(parent)
        self._name = name or socket_name()
        self._server: QLocalServer | None = None
        self._connections: list[QLocalSocket] = []

    def listen(self) -> bool:
        """Start accepting forwarded links. True if this process now owns the socket.

        A crash leaves the socket file behind on Linux and every later launch
        would fail to listen -- and silently stop handling links. removeServer()
        clears that stale entry. It cannot steal a live one: a running instance
        would have answered forward_to_running_instance() first, and this is
        only reached when nothing did.
        """
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        if not server.listen(self._name):
            _log.warning(
                "deep links disabled: cannot listen on %s: %s", self._name, server.errorString()
            )
            return False
        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def _on_connection(self) -> None:
        assert self._server is not None
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        # Keep a reference: nextPendingConnection() hands back a child of the
        # server, but readyRead fires later and a dropped Python reference to a
        # parented QObject is fine -- the list is for deterministic cleanup.
        self._connections.append(socket)
        socket.readyRead.connect(lambda: self._on_ready_read(socket))
        socket.disconnected.connect(lambda: self._forget(socket))

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        payload = bytes(socket.readAll().data()).decode("utf-8", errors="replace").strip()
        if payload:
            self.linkReceived.emit(payload)

    def _forget(self, socket: QLocalSocket) -> None:
        if socket in self._connections:
            self._connections.remove(socket)
        socket.deleteLater()

    def install_macos_handler(self, app: QCoreApplication) -> None:
        """Catch the QFileOpenEvent macOS delivers for a registered scheme.

        This is the only delivery path on macOS -- the OS never re-execs the
        binary with the URL in argv -- so without it a link does nothing there.
        """
        app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FileOpen and isinstance(event, QFileOpenEvent):
            url = event.url().toString()
            if url:
                self.linkReceived.emit(url)
                return True
        return super().eventFilter(watched, event)
