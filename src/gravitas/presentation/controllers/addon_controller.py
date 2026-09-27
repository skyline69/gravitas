"""QObject bridge: install an addon by URL, then bind + refresh the UI."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import Property, QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.install_addon import InstallAddon
from gravitas.domain.errors import GravitasError


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class AddonController(QObject):
    errorOccurred = Signal(str)
    addonInstalled = Signal(str)
    installingChanged = Signal()

    def __init__(
        self,
        install: InstallAddon,
        catalog_controller: _RefreshesCatalog,
    ) -> None:
        super().__init__()
        self._install = install
        self._catalog_controller = catalog_controller
        self._installing = False

    @Property(bool, notify=installingChanged)
    def installing(self) -> bool:
        """True from Add-click to catalog refresh — drives the busy spinner
        (manifest fetch + first catalog load take visible seconds)."""
        return self._installing

    def _set_installing(self, value: bool) -> None:
        if self._installing != value:
            self._installing = value
            self.installingChanged.emit()

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def addAddon(self, url: str) -> None:
        await self.install(url)

    async def install(self, url: str) -> None:
        """Install and bring the UI up to date. The one install path.

        A plain coroutine, not the slot: the deep-link flow awaits this so its
        own confirmation coroutine can finish afterwards, and everything that
        installs an addon -- Settings field, deep link -- lands on the same
        refresh and persistence wiring.
        """
        url = url.strip()
        if not url:
            return
        self._set_installing(True)
        try:
            try:
                manifest = await self._install(url)
            except GravitasError as exc:
                self.errorOccurred.emit(str(exc))
                return
            # Deterministic refresh: await load_catalog() directly (same path
            # bootstrap uses in main.py) rather than firing the asyncSlot
            # refresh() and letting it race with the rest of this coroutine.
            await self._catalog_controller.load_catalog()
            self.addonInstalled.emit(manifest.name)
        finally:
            self._set_installing(False)
