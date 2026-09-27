"""Keep the screen on while a video plays (the IdleInhibitor port).

mpv would normally do this itself (`stop-screensaver`), but it only can when it
owns the window, and here it does not: libmpv renders into our Qt scene through
the render API, so nothing ever told the desktop a video was playing and the
screen dimmed and locked mid-film. Each platform has its own way to ask:

* **Linux** -- `org.freedesktop.ScreenSaver.Inhibit` first, then the XDG
  desktop portal's `Inhibit` (flag 8, "idle"). The first names the app in
  Plasma's battery applet; the second is the one that works inside the Flatpak
  (the sandbox cannot see the ScreenSaver name) and on GNOME. Both hold only
  while the D-Bus connection that asked stays open, which is why this cannot
  shell out to `gdbus`, and both want a `u` (uint32) that PySide6's QtDBus
  cannot produce from Python -- it sends `i`, and the portal rejects the call.
  So it speaks D-Bus through jeepney. Checked on Plasma 6.7: each appears in
  PowerDevil's ActiveInhibitions as an "idle" block (about 5s after the call:
  PowerDevil deliberately ignores inhibitions shorter than that) and is gone
  on UnInhibit / Request.Close.
* **Windows** -- `SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED
  | ES_SYSTEM_REQUIRED)`. The state belongs to the calling thread, which is one
  more reason every call goes through the one long-lived worker below.
* **macOS** -- an IOKit `PreventUserIdleDisplaySleep` power assertion.

All of it runs on one daemon worker thread: a D-Bus round trip must never cost
the GUI thread a frame, and jeepney's blocking connection must only ever be
used from one thread. `set_inhibited` only records what is wanted; the worker
applies the latest wish and skips any it has already been overtaken by.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

_log = logging.getLogger(__name__)

_APP_NAME = "Gravitas"
_REASON = "Playing video"


class _Backend(Protocol):
    def inhibit(self) -> bool: ...
    def release(self) -> None: ...


class _LinuxBackend:
    _SCREENSAVER = ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver")
    _PORTAL = "org.freedesktop.portal.Desktop"
    _IDLE = 8  # org.freedesktop.portal.Inhibit flags: 8 = idle

    def __init__(self) -> None:
        self._connection: Any = None
        # How the current inhibition was obtained, and its handle: a
        # ScreenSaver cookie (int) or a portal Request object path (str).
        self._held: tuple[str, Any] | None = None

    def _call(
        self,
        bus_name: str,
        path: str,
        interface: str,
        method: str,
        signature: str | None = None,
        body: tuple[Any, ...] = (),
    ) -> tuple[Any, ...]:
        from jeepney import (  # type: ignore[import-untyped,import-not-found,unused-ignore]
            DBusAddress,
            MessageType,
            new_method_call,
        )
        from jeepney.io.blocking import (  # type: ignore[import-untyped,import-not-found,unused-ignore]
            open_dbus_connection,
        )

        if self._connection is None:
            self._connection = open_dbus_connection(bus="SESSION")
        address = DBusAddress(path, bus_name=bus_name, interface=interface)
        reply = self._connection.send_and_get_reply(
            new_method_call(address, method, signature, body), timeout=3
        )
        if reply.header.message_type == MessageType.error:
            raise RuntimeError(f"{bus_name} {method}: {reply.body}")
        return tuple(reply.body)

    def inhibit(self) -> bool:
        name, path = self._SCREENSAVER
        try:
            (cookie,) = self._call(name, path, name, "Inhibit", "ss", (_APP_NAME, _REASON))
            self._held = ("screensaver", cookie)
            return True
        except Exception as exc:
            _log.debug("ScreenSaver inhibit unavailable (%s); trying the portal", exc)
        try:
            (handle,) = self._call(
                self._PORTAL,
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.portal.Inhibit",
                "Inhibit",
                "sua{sv}",
                ("", self._IDLE, {"reason": ("s", _REASON)}),
            )
            self._held = ("portal", handle)
            return True
        except Exception as exc:
            _log.info("cannot keep the screen awake: no ScreenSaver or portal inhibit (%s)", exc)
            return False

    def release(self) -> None:
        held, self._held = self._held, None
        if held is None:
            return
        kind, handle = held
        try:
            if kind == "screensaver":
                name, path = self._SCREENSAVER
                self._call(name, path, name, "UnInhibit", "u", (handle,))
            else:
                self._call(self._PORTAL, handle, "org.freedesktop.portal.Request", "Close")
        except Exception as exc:
            # Closing the connection would drop it too; this only logs, since
            # the next inhibit reuses the connection and a stale inhibition
            # ends with the process at the latest.
            _log.warning("releasing the idle inhibition failed: %s", exc)


class _WindowsBackend:
    _ES_CONTINUOUS = 0x80000000
    _ES_SYSTEM_REQUIRED = 0x00000001
    _ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self) -> None:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._set: Callable[[int], int] = kernel32.SetThreadExecutionState

    def inhibit(self) -> bool:
        flags = self._ES_CONTINUOUS | self._ES_SYSTEM_REQUIRED | self._ES_DISPLAY_REQUIRED
        return bool(self._set(flags))

    def release(self) -> None:
        self._set(self._ES_CONTINUOUS)


class _MacBackend:
    _UTF8 = 0x08000100  # kCFStringEncodingUTF8
    _LEVEL_ON = 255  # kIOPMAssertionLevelOn

    def __init__(self) -> None:
        self._iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        self._cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        self._cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self._cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        self._cf.CFRelease.argtypes = [ctypes.c_void_p]
        self._iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self._iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
        self._assertion: int | None = None

    def _cfstring(self, text: str) -> int:
        return int(self._cf.CFStringCreateWithCString(None, text.encode(), self._UTF8))

    def inhibit(self) -> bool:
        kind = self._cfstring("PreventUserIdleDisplaySleep")
        name = self._cfstring(f"{_APP_NAME}: {_REASON}")
        assertion = ctypes.c_uint32(0)
        try:
            result = self._iokit.IOPMAssertionCreateWithName(
                kind, self._LEVEL_ON, name, ctypes.byref(assertion)
            )
        finally:
            self._cf.CFRelease(kind)
            self._cf.CFRelease(name)
        if result != 0:
            return False
        self._assertion = assertion.value
        return True

    def release(self) -> None:
        if self._assertion is not None:
            self._iokit.IOPMAssertionRelease(self._assertion)
            self._assertion = None


def _backend_for(platform: str) -> _Backend | None:
    if platform.startswith("linux"):
        return _LinuxBackend()
    if platform == "win32":
        return _WindowsBackend()
    if platform == "darwin":
        return _MacBackend()
    return None


class IdleInhibitor:
    """The IdleInhibitor port: one wish at a time, applied off the GUI thread."""

    def __init__(self, backend_factory: Callable[[], _Backend | None]) -> None:
        self._factory = backend_factory
        self._backend: _Backend | None = None
        self._wanted = False
        self._applied = False
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._run, name="idle-inhibitor", daemon=True)
        self._thread.start()

    @classmethod
    def for_platform(cls, platform: str = sys.platform) -> IdleInhibitor:
        return cls(lambda: _backend_for(platform))

    def set_inhibited(self, inhibited: bool) -> None:
        with self._condition:
            self._wanted = inhibited
            self._condition.notify_all()

    def _run(self) -> None:
        try:
            self._backend = self._factory()
        except Exception as exc:
            _log.info("idle inhibition unavailable on this system: %s", exc)
            self._backend = None
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._wanted != self._applied)
                wanted = self._wanted
            self._apply(wanted)
            # Recorded as applied whatever the backend said: one that cannot
            # inhibit must not be retried in a loop, and the next change of
            # wish is a fresh attempt anyway.
            with self._condition:
                self._applied = wanted
                self._condition.notify_all()

    def _apply(self, wanted: bool) -> None:
        if self._backend is None:
            return
        try:
            if wanted:
                if self._backend.inhibit():
                    _log.info("keeping the screen awake while the video plays")
            else:
                self._backend.release()
        except Exception as exc:
            _log.warning("idle inhibition failed: %s", exc)

    def wait_idle(self, timeout: float = 2.0) -> bool:
        """Block until the latest wish has been applied. For tests."""
        with self._condition:
            return self._condition.wait_for(lambda: self._wanted == self._applied, timeout)
