"""Picture-in-picture on KDE Plasma under Wayland: above everything, no frame.

Main.qml asks for both with window flags (`WindowStaysOnTopHint`,
`FramelessWindowHint`), which X11, Windows and macOS honour. Wayland has no
protocol for "stay on top" at all, and a frameless flag set on a window that
is already shown does not take either: checked on KWin 6.7 by asking KWin
itself, the tile came out with `keepAbove=false` and `noBorder=false`, a
title bar on a 480px video, and anything could cover it.

KWin will do both for a script, and scripts are loaded over the session bus
(`org.kde.KWin /Scripting`). While PiP is on, one stays loaded: it sets
`keepAbove` and `noBorder` on every normal window of this app, and on any it
opens afterwards -- changing a window's flags can recreate its surface, and
the recreated one arrives as a new KWin window. Leaving PiP unloads it and
runs a second that hands both back.

Windows are matched by app id (the desktop file name, which Wayland reports
as the window's resourceClass), not by process id. Inside a Flatpak the app
has its own pid namespace, so the pid it knows is not the one KWin sees --
matched by pid, the script found nothing and the tile was never pinned.
Gravitas is single-instance, so the app id names exactly this window. The
Flatpak also needs `--talk-name=org.kde.KWin` to reach the bus name at all.

Anywhere else -- X11, another compositor, no KWin on the bus -- this does
nothing, and the flags are the whole of it. GNOME under Wayland has no such
interface: the tile floats, frameless, but can be covered.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PySide6.QtGui import QGuiApplication

from gravitas.infrastructure.paths import cache_dir

_log = logging.getLogger(__name__)

_SERVICE = "org.kde.KWin"
_PATH = "/Scripting"
_INTERFACE = "org.kde.kwin.Scripting"

# KWin 6 lists windows with windowList(); Plasma 5 called it clientList().
_WINDOWS = "(workspace.windowList ? workspace.windowList() : workspace.clientList())"

# Every call is `(method, *args) -> reply`, where a reply of None is a failure.
# A seam so the logic can be tested without a KWin on the bus.
Caller = Callable[..., Any]


def _is_ours(app_id: str) -> str:
    # resourceClass has been reported lowercased by some KWin versions, so the
    # comparison is case-insensitive. json.dumps makes the id a JS string
    # literal whatever it contains.
    return f"String(window.resourceClass).toLowerCase() === {json.dumps(app_id.lower())}"


def float_script(app_id: str) -> str:
    return f"""// Gravitas picture-in-picture: keep this app's window on top, frameless.
function float(window) {{
    if (!({_is_ours(app_id)}) || !window.normalWindow)
        return;
    window.keepAbove = true;
    window.noBorder = true;
}}
{_WINDOWS}.forEach(float);
workspace.windowAdded.connect(float);
"""


def restore_script(app_id: str) -> str:
    return f"""// Gravitas picture-in-picture ended: give the window back its frame.
{_WINDOWS}.forEach(function (window) {{
    if (!({_is_ours(app_id)}) || !window.normalWindow)
        return;
    window.keepAbove = false;
    window.noBorder = false;
}});
"""


def _dbus_caller() -> Caller:
    bus = QDBusConnection.sessionBus()
    scripting = QDBusInterface(_SERVICE, _PATH, _INTERFACE, bus)

    def call(method: str, *args: object) -> object | None:
        reply = scripting.call(method, *args)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            _log.warning("KWin %s failed: %s", method, reply.errorMessage())
            return None
        values = reply.arguments()
        return values[0] if values else True

    return call


def kwin_available() -> bool:
    """Wayland, with KWin answering on the session bus."""
    if QGuiApplication.platformName() != "wayland":
        return False
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    interface = bus.interface()
    return interface is not None and bool(interface.isServiceRegistered(_SERVICE).value())


class KWinFloatingWindow:
    """FloatingWindow for KWin: loads a script while the tile is up."""

    def __init__(self, call: Caller, script_dir: Path, app_id: str, pid: int | None = None) -> None:
        self._call = call
        self._dir = script_dir
        self._app_id = app_id
        # Only names the scripts. NOT unique across runs: inside a Flatpak the
        # app is pid 2 in its own namespace every time, so every session uses
        # the same two names -- see set_floating and sweep_leftovers.
        self._pid = os.getpid() if pid is None else pid
        # The name the loaded script is known by, so the next call (and
        # shutdown) can unload it. KWin refuses a second load under a name
        # that is still loaded.
        self._loaded: str | None = None

    @classmethod
    def for_session(cls) -> KWinFloatingWindow | None:
        """The adapter when this session has a KWin to talk to, else None."""
        if not kwin_available():
            return None
        app_id = QGuiApplication.desktopFileName()
        if not app_id:
            return None
        window = cls(_dbus_caller(), cache_dir() / "kwin", app_id)
        window.sweep_leftovers()
        return window

    def sweep_leftovers(self) -> None:
        """Unload every script an earlier run of this install left in KWin.

        A run that ends without aboutToQuit -- killed, Ctrl+C, crashed --
        never unloads its script, and KWin keeps it until KWin restarts. A
        leftover float script is the bad one: its windowAdded hook takes the
        frame off and pins every Gravitas window opened after it. Their names
        are the files this adapter wrote, so those are read back here.
        """
        try:
            for path in sorted(self._dir.glob("gravitas-pip-*.js")):
                self._call("unloadScript", path.stem)
                path.unlink(missing_ok=True)
        except Exception:
            _log.debug("sweeping leftover KWin scripts failed", exc_info=True)

    def set_floating(self, floating: bool) -> None:
        try:
            self._unload()
            name = f"gravitas-pip-{'float' if floating else 'restore'}-{self._pid}"
            source = float_script(self._app_id) if floating else restore_script(self._app_id)
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / f"{name}.js"
            path.write_text(source)
            # KWin refuses a load under a name that is still loaded (it answers
            # -1), and a name can be taken by a run that died without
            # unloading: in the Flatpak every run is pid 2, so the next
            # session's restore script was refused and leaving PiP left the
            # window frameless and on top. Unloading an absent name is a no-op.
            self._call("unloadScript", name)
            script_id = self._call("loadScript", str(path), name)
            if script_id is None or int(script_id) < 0:
                _log.warning("KWin would not load the picture-in-picture script")
                return
            self._loaded = name
            self._call("start")
        except Exception:
            # A desktop that cannot pin the tile leaves an ordinary window, and
            # that is no reason to take playback down with it.
            _log.warning("could not ask KWin to float the window", exc_info=True)

    def shutdown(self) -> None:
        """Unload whatever is still loaded; wired to aboutToQuit. A script left
        behind would pin the next Gravitas that happens to get this pid."""
        try:
            self._unload()
        except Exception:
            _log.debug("unloading the KWin script failed on the way out", exc_info=True)

    def _unload(self) -> None:
        if self._loaded is not None:
            self._call("unloadScript", self._loaded)
            self._loaded = None
