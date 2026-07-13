"""QObject bridge: manage installed addons for the Settings page."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import PersistedSettings
from gravitas.domain.ports import SettingsStore
from gravitas.presentation.models.addon_list_model import AddonListModel


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class _KeyHolder(Protocol):
    key: str | None


class SettingsController(QObject):
    errorOccurred = Signal(str)
    addonsChanged = Signal()
    tmdbKeyChanged = Signal()

    def __init__(
        self,
        uninstall: UninstallAddon,
        repo: AddonRepository,
        model: AddonListModel,
        catalog_controller: _RefreshesCatalog,
        key_holder: _KeyHolder | None = None,
        store: SettingsStore | None = None,
    ) -> None:
        super().__init__()
        self._uninstall = uninstall
        self._repo = repo
        self._model = model
        self._catalog_controller = catalog_controller
        self._key_holder = key_holder
        self._store = store

    @Property(str, notify=tmdbKeyChanged)
    def tmdbKey(self) -> str:
        if self._key_holder is not None and self._key_holder.key:
            return self._key_holder.key
        return ""

    @Slot(str)
    def setTmdbKey(self, key: str) -> None:
        if self._key_holder is not None:
            self._key_holder.key = key.strip() or None
            self.tmdbKeyChanged.emit()
        self.persist()

    @Slot()
    def persist(self) -> None:
        """Write the current user state (addon URLs + TMDB key) to the store."""
        if self._store is None:
            return
        key = self._key_holder.key if self._key_holder is not None else None
        self._store.save(
            PersistedSettings(addon_urls=tuple(self._repo.user_addon_urls()), tmdb_key=key)
        )

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
        self.persist()
