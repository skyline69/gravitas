"""QObject bridge: manage installed addons for the Settings page."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.addon_list_model import AddonListModel


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class SettingsController(QObject):
    errorOccurred = Signal(str)
    addonsChanged = Signal()

    def __init__(
        self,
        uninstall: UninstallAddon,
        repo: AddonRepository,
        model: AddonListModel,
        catalog_controller: _RefreshesCatalog,
    ) -> None:
        super().__init__()
        self._uninstall = uninstall
        self._repo = repo
        self._model = model
        self._catalog_controller = catalog_controller

    @Slot()
    def refreshAddons(self) -> None:
        installed = self._repo.installed()
        protected = {m.id for m in installed if self._repo.is_protected(m.id)}
        self._model.set_addons(installed, protected)
        self.addonsChanged.emit()

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def removeAddon(self, addon_id: str) -> None:
        try:
            await self._uninstall(addon_id)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
            return
        await self._catalog_controller.load_catalog()
        self.refreshAddons()
