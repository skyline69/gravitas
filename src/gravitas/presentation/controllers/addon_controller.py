"""QObject bridge: install an addon by URL, then bind + refresh the UI."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.install_addon import InstallAddon
from gravitas.domain.errors import GravitasError


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class AddonController(QObject):
    errorOccurred = Signal(str)
    addonInstalled = Signal(str)

    def __init__(
        self,
        install: InstallAddon,
        catalog_controller: _RefreshesCatalog,
    ) -> None:
        super().__init__()
        self._install = install
        self._catalog_controller = catalog_controller

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
