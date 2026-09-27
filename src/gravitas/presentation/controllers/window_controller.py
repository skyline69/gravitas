"""QObject bridge: the parts of the main window's behaviour that belong to the
desktop rather than to Qt."""

from __future__ import annotations

from PySide6.QtCore import QObject, Slot

from gravitas.domain.ports import FloatingWindow


class WindowController(QObject):
    def __init__(self, floating: FloatingWindow | None = None) -> None:
        super().__init__()
        self._floating = floating

    @Slot(bool)
    def setFloating(self, floating: bool) -> None:
        """Picture-in-picture on (True) or off: above other windows and
        without a frame, where the desktop needs asking beyond window flags."""
        if self._floating is not None:
            self._floating.set_floating(floating)
