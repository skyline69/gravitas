"""Open URLs in the user's browser, surviving frozen-bundle environments.

On Linux QDesktopServices.openUrl spawns `xdg-open`, and the child inherits
this process's environment. In a PyInstaller bundle (AppImage) the bootloader
points LD_LIBRARY_PATH & co. at the bundle's private libs — a browser launched
under that environment links against them and dies before showing a window,
which is why "open in browser" silently did nothing. The bootloader saves each
variable it touches as <NAME>_ORIG, so the host's values can be restored just
around the spawn.

Inside Flatpak Qt never spawns anything — it hands the URL to the XDG desktop
portal over DBus — so the swap is a harmless no-op there. Unfrozen dev runs
skip it entirely.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager

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


@contextmanager
def _host_environment() -> Iterator[None]:
    """Swap the bundle's env vars for the host's around a child spawn."""
    if not getattr(sys, "frozen", False):
        yield
        return
    saved: dict[str, str | None] = {}
    for var in _BUNDLE_VARS:
        saved[var] = os.environ.get(var)
        original = os.environ.get(f"{var}_ORIG")
        if original is not None:
            os.environ[var] = original
        else:
            os.environ.pop(var, None)
    try:
        yield
    finally:
        for var, value in saved.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value


def _launch(url: str) -> bool:
    return bool(QDesktopServices.openUrl(QUrl(url)))


def open_in_browser(url: str, *, launch: Callable[[str], bool] | None = None) -> bool:
    """Open `url` externally. `launch` is a test seam over QDesktopServices."""
    if not url:
        return False
    with _host_environment():
        return (launch if launch is not None else _launch)(url)
