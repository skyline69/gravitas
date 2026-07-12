"""QObject bridge: install an addon by URL, then bind + refresh the UI."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.install_addon import InstallAddon
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import AddonManifest


class _BindsManifest(Protocol):
    def bind_manifest(self, manifest: AddonManifest) -> None: ...


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class AddonController(QObject):
    errorOccurred = Signal(str)
    addonInstalled = Signal(str)

    def __init__(
        self,
        install: InstallAddon,
        detail_controller: _BindsManifest,
        catalog_controller: _RefreshesCatalog,
    ) -> None:
        super().__init__()
        self._install = install
        self._detail_controller = detail_controller
        self._catalog_controller = catalog_controller

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def addAddon(self, url: str) -> None:
        url = url.strip()
        if not url:
            return
        try:
            manifest = await self._install(url)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
            return
        self._detail_controller.bind_manifest(manifest)
        # Deterministic refresh: await load_catalog() directly (same path
        # bootstrap uses in main.py) rather than firing the asyncSlot
        # refresh() and letting it race with the rest of this coroutine.
        await self._catalog_controller.load_catalog()
        self.addonInstalled.emit(manifest.name)
