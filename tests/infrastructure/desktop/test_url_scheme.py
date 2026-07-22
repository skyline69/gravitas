"""Real QLocalServer/QLocalSocket round-trips: the forwarding path is the whole
point of the module, and a fake socket would only prove the fake works."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtNetwork import QLocalServer

from gravitas.infrastructure.desktop.url_scheme import (
    ACTIVATE,
    DeepLinkListener,
    forward_to_running_instance,
    socket_name,
)

_LINK = "stremio://addon.example/manifest.json"


@pytest.fixture
def name() -> Iterator[str]:
    # A per-test socket name so a stray server from one test cannot answer
    # another's connect and turn a failure into a pass.
    unique = f"gravitas-test-{os.getpid()}-{id(object())}"
    yield unique
    QLocalServer.removeServer(unique)


def _spin(predicate: object, timeout_ms: int = 2000) -> None:
    """Turn the event loop until `predicate()` or the timeout.

    Condition-based, not a fixed sleep: the socket is genuinely asynchronous,
    and a sleep long enough to be reliable is long enough to slow the suite.
    """
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    deadline = QTimer()
    deadline.setSingleShot(True)

    def check() -> None:
        if predicate():  # type: ignore[operator]
            loop.quit()

    timer.timeout.connect(check)
    deadline.timeout.connect(loop.quit)
    timer.start()
    deadline.start(timeout_ms)
    loop.exec()
    timer.stop()


def test_forward_returns_false_when_nothing_is_listening(qapp: object, name: str) -> None:
    # The primary launch path: no instance running, so the caller must start
    # normally rather than exit thinking it forwarded.
    assert forward_to_running_instance(_LINK, name=name) is False


def test_listener_receives_a_forwarded_link(qapp: object, name: str) -> None:
    received: list[str] = []
    listener = DeepLinkListener(name=name)
    assert listener.listen() is True
    listener.linkReceived.connect(received.append)

    assert forward_to_running_instance(_LINK, name=name) is True
    _spin(lambda: received)

    assert received == [_LINK]


def test_listener_receives_several_links_in_one_session(qapp: object, name: str) -> None:
    received: list[str] = []
    listener = DeepLinkListener(name=name)
    assert listener.listen() is True
    listener.linkReceived.connect(received.append)

    for i in range(3):
        assert forward_to_running_instance(f"{_LINK}?n={i}", name=name) is True
        _spin(lambda i=i: len(received) > i)  # type: ignore[misc]

    assert received == [f"{_LINK}?n={i}" for i in range(3)]


def test_listen_reclaims_a_socket_left_by_a_crash(qapp: object, name: str) -> None:
    # A crashed instance leaves the socket file behind on Linux. Without
    # removeServer() every later launch fails to listen and silently stops
    # handling links -- with no crash to point at.
    stale = QLocalServer()
    assert stale.listen(name) is True
    stale.close()  # leaves the filesystem entry

    listener = DeepLinkListener(name=name)
    assert listener.listen() is True

    received: list[str] = []
    listener.linkReceived.connect(received.append)
    assert forward_to_running_instance(_LINK, name=name) is True
    _spin(lambda: received)
    assert received == [_LINK]


def test_second_launch_without_a_link_asks_the_running_one_to_surface(
    qapp: object, name: str
) -> None:
    # The single-instance path: a plain `gravitas` with an instance already up
    # must not open a second window, it must raise the first one.
    activations: list[int] = []
    links: list[str] = []
    listener = DeepLinkListener(name=name)
    assert listener.listen() is True
    listener.activateRequested.connect(lambda: activations.append(1))
    listener.linkReceived.connect(links.append)

    assert forward_to_running_instance(ACTIVATE, name=name) is True
    _spin(lambda: activations)

    assert activations == [1]
    # ACTIVATE is not a URL; routing it as one would hand the deep-link
    # controller garbage to resolve.
    assert links == []


def test_first_launch_without_a_link_is_the_primary(qapp: object, name: str) -> None:
    # Nothing listening: the caller must boot normally rather than exit
    # believing it forwarded and leave the user with no app at all.
    assert forward_to_running_instance(ACTIVATE, name=name) is False


def test_socket_name_is_scoped_to_the_user(qapp: object) -> None:
    # QLocalServer sockets live in a shared /tmp on Unix and a machine-global
    # \\.\pipe\ namespace on Windows; an unscoped name would let one user's
    # link reach another user's Gravitas.
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        assert str(getuid()) in socket_name()
    else:
        assert socket_name() != "gravitas-deeplink-"


def test_socket_name_falls_back_to_the_account_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # The Windows path: no uid exists there, so the account name scopes the
    # pipe -- reduced to characters the namespace accepts, since a real
    # account can be `DOMAIN\Ada Lovelace`.
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.setenv("USERNAME", "Ada Lovelace")
    assert socket_name() == "gravitas-deeplink-Ada-Lovelace"

    monkeypatch.delenv("USERNAME")
    monkeypatch.delenv("USER", raising=False)
    assert socket_name() == "gravitas-deeplink-default"


def test_macos_file_open_event_is_forwarded(qapp: QCoreApplication, name: str) -> None:
    # The only delivery path on macOS: the OS posts QFileOpenEvent to the
    # running app rather than re-execing it with an argv.
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFileOpenEvent

    received: list[str] = []
    listener = DeepLinkListener(name=name)
    listener.linkReceived.connect(received.append)
    listener.install_macos_handler(qapp)

    QCoreApplication.sendEvent(qapp, QFileOpenEvent(QUrl(_LINK)))

    assert received == [_LINK]
    qapp.removeEventFilter(listener)
