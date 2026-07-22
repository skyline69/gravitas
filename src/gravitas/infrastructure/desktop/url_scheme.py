"""Keep Gravitas to one process, and get a stremio:// URL into it.

Both jobs ride the same per-user local socket. Whoever owns it is *the*
instance; anything that fails to claim it hands its payload over and exits.
Two Gravitas windows with separate SQLite and settings state is not a thing a
user asked for -- the second one would silently fight the first over the same
files.

The payload is either a stremio:// URL or `ACTIVATE`:

- **A link, Linux**: the browser executes `gravitas <url>`. That is a *new
  process* while one is very likely already running, so it forwards the URL.
- **A link, macOS**: the OS does not re-exec anything. It posts a
  QFileOpenEvent to the already-running app, and at cold start delivers it
  once the event loop is up.
- **A plain launch**: no URL at all -- the user clicked the icon a second
  time. `ACTIVATE` says "you are already running, come to the front".

This module owns all of it, and everything above it sees two signals: a URL
arrived, or someone asked us to surface.
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

# "I am a second launch with nothing to hand over -- raise your window."
# Cannot collide with a link: every forwarded URL starts with `stremio://`.
ACTIVATE = "activate"


def socket_name() -> str:
    """Per-user socket name.

    Scoped by user because the namespace is shared machine-wide: QLocalServer
    puts its socket in a common /tmp on Unix and behind a global \\\\.\\pipe\\
    name on Windows. Unscoped, one user's link would reach -- or simply block
    -- another user's Gravitas. uid where there is one, the account name
    elsewhere, reduced to characters both namespaces accept.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return f"gravitas-deeplink-{getuid()}"
    account = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    scope = "".join(c if c.isalnum() or c in "-_" else "-" for c in account)
    return f"gravitas-deeplink-{scope or 'default'}"


def forward_to_running_instance(payload: str, *, name: str | None = None) -> bool:
    """Hand `payload` -- a stremio:// URL or ACTIVATE -- to a running Gravitas.

    True if one took it, and the caller must exit: an instance already owns the
    databases. False means nothing is listening and the caller is the primary.
    Called before any GUI exists, so it must never raise: a failure here has to
    end as "start normally", not as a crash on launch.
    """
    socket = QLocalSocket()
    socket.connectToServer(name or socket_name())
    if not socket.waitForConnected(_CONNECT_TIMEOUT_MS):
        return False
    try:
        socket.write(payload.encode("utf-8"))
        # flush() then wait: this runs before any event loop exists (the GUI is
        # not built yet), so nothing else will ever push these bytes out.
        socket.flush()
        if not socket.waitForBytesWritten(_WRITE_TIMEOUT_MS):
            # Still True: the connect proved an instance is alive and holding
            # the databases. Dropping one link beats booting a second copy.
            _log.warning("connected to the running instance but could not send %s", payload)
        return True
    finally:
        socket.disconnectFromServer()


class DeepLinkListener(QObject):
    """Owns the single-instance socket.

    Emits linkReceived for every stremio:// URL this process is handed, and
    activateRequested when a second launch asks us to come to the front.
    """

    linkReceived = Signal(str)
    activateRequested = Signal()

    def __init__(self, parent: QObject | None = None, *, name: str | None = None) -> None:
        super().__init__(parent)
        self._name = name or socket_name()
        self._server: QLocalServer | None = None
        self._connections: list[QLocalSocket] = []

    def listen(self) -> bool:
        """Claim the instance socket. True if this process now owns it.

        A crash leaves the socket file behind on Linux and every later launch
        would fail to listen -- and silently stop handling links, and stop
        holding the single-instance lock. removeServer() clears that stale
        entry. It cannot steal a live one: a running instance would have
        answered forward_to_running_instance() first, and this is only reached
        when nothing did.
        """
        QLocalServer.removeServer(self._name)
        server = QLocalServer(self)
        if not server.listen(self._name):
            _log.warning(
                "single-instance lock and deep links disabled: cannot listen on %s: %s",
                self._name,
                server.errorString(),
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
        # Membership in the list also means "this connection has not delivered
        # yet", which is what keeps the two paths below from firing twice.
        self._connections.append(socket)
        socket.readyRead.connect(lambda: self._deliver(socket))
        socket.disconnected.connect(lambda: self._close(socket))

    def _deliver(self, socket: QLocalSocket) -> None:
        """Read the queued payload and route it. One per connection: the
        forwarder writes a single line and hangs up immediately."""
        if socket not in self._connections:
            return
        payload = bytes(socket.readAll().data()).decode("utf-8", errors="replace").strip()
        if not payload:
            return
        self._connections.remove(socket)
        if payload == ACTIVATE:
            self.activateRequested.emit()
        else:
            self.linkReceived.emit(payload)
        socket.deleteLater()

    def _close(self, socket: QLocalSocket) -> None:
        """The peer hung up -- drain before dropping the connection.

        Load-bearing on Windows, where the transport is a named pipe: the
        forwarder writes, waits for the bytes to be handed over, and closes,
        and Qt then delivers `disconnected` with the payload still queued and
        `readyRead` never following. Forgetting the socket here without reading
        it first loses the link outright -- every stremio:// click into a
        running instance did nothing.
        """
        self._deliver(socket)
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
