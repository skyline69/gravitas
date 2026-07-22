"""Open URLs in the user's browser, surviving frozen-bundle environments.

In a PyInstaller bundle (AppImage) the bootloader points LD_LIBRARY_PATH & co.
at the bundle's private libs. Anything spawned from here inherits them and
links against libraries built on the release machine: on a Fedora host,
`kde-open` dies with "libstdc++.so.6: version GLIBCXX_3.4.32 not found" before
it can hand the URL to a browser, which is why "open in browser" did nothing.

The obvious fix — swap the variables back around QDesktopServices.openUrl —
does not work, and measurably so. With LD_LIBRARY_PATH unset, a subprocess
launched from the same line sees it unset, while Qt's child still receives the
bundle path: Qt spawns the opener from an environment it captured earlier, so
mutating os.environ cannot reach it. So the opener is spawned here instead,
with the child's environment passed explicitly.

Inside Flatpak Qt hands the URL to the XDG desktop portal over DBus and none
of this applies; the bundle variables are absent there anyway, so the child
environment is simply a copy of this process's.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

# Every variable the PyInstaller bootloader rewrites that could poison a
# spawned browser. Qt/QML paths matter for browsers that are themselves Qt
# apps; the Python ones for anything wrapping a Python interpreter.
_BUNDLE_VARS = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "QT_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QML_IMPORT_PATH",
    "PYTHONPATH",
    "PYTHONHOME",
)

# Tried in order, first one present on PATH wins. xdg-open is the standard and
# delegates to the desktop's own handler; gio covers GTK desktops where
# xdg-open is missing, and the two desktop-specific openers are last resorts.
_OPENERS = (["xdg-open"], ["gio", "open"], ["kde-open"], ["gnome-open"])


def host_environment() -> dict[str, str]:
    """The environment a spawned opener should get.

    Unfrozen, that is simply this process's. Frozen, every bundle variable is
    replaced by the host value the bootloader saved as <NAME>_ORIG, or dropped
    when the host had none.
    """
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        return env
    for var in _BUNDLE_VARS:
        original = env.pop(f"{var}_ORIG", None)
        if original is not None:
            env[var] = original
        else:
            env.pop(var, None)
    return env


def _launch(url: str, env: dict[str, str]) -> bool:
    """Spawn a desktop opener with `env`, or fall back to Qt.

    The child is detached (start_new_session) so it outlives this process, and
    its output is discarded: an opener that writes to a terminal Gravitas may
    not have would otherwise block on a full pipe.
    """
    # Windows and macOS have one correct answer each (ShellExecute, LaunchServices)
    # and Qt knows both. Never probe the opener list there: a machine with Git
    # Bash, MSYS or WSL interop on PATH has an `xdg-open` that would win the
    # search and then fail to open anything.
    if sys.platform in ("win32", "darwin"):
        return bool(QDesktopServices.openUrl(QUrl(url)))
    for opener in _OPENERS:
        executable = shutil.which(opener[0], path=env.get("PATH", os.defpath))
        if executable is None:
            continue
        try:
            # Fixed argv with the URL as an argument, never a shell string.
            subprocess.Popen(
                [executable, *opener[1:], url],
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            continue
        return True
    # No opener on PATH: Qt's own attempt is all that is left.
    return bool(QDesktopServices.openUrl(QUrl(url)))


def open_in_browser(
    url: str, *, launch: Callable[[str, dict[str, str]], bool] | None = None
) -> bool:
    """Open `url` externally. `launch` is a test seam over the spawn."""
    if not url:
        return False
    return (launch if launch is not None else _launch)(url, host_environment())
