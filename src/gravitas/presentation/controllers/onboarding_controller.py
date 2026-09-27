"""QObject bridge: first-run onboarding state for the QML wizard."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Property, QObject, Signal, Slot


class _FlagHolder(Protocol):
    done: bool


class OnboardingController(QObject):
    activeChanged = Signal()

    def __init__(self, holder: _FlagHolder, persist: Callable[[], None]) -> None:
        super().__init__()
        self._holder = holder
        self._persist = persist

    @Property(bool, notify=activeChanged)
    def active(self) -> bool:
        """True until the wizard is finished or skipped — drives the overlay."""
        return not self._holder.done

    @Slot()
    def complete(self) -> None:
        if self._holder.done:
            return
        self._holder.done = True
        self.activeChanged.emit()
        self._persist()
