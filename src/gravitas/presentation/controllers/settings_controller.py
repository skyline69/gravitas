"""QObject bridge: manage installed addons for the Settings page."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import PersistedSettings, SubtitleStyle
from gravitas.domain.ports import SettingsStore
from gravitas.presentation.models.addon_list_model import AddonListModel


class _RefreshesCatalog(Protocol):
    async def load_catalog(self) -> None: ...


class _KeyHolder(Protocol):
    key: str | None


class _StyleHolder(Protocol):
    style: SubtitleStyle


class SettingsController(QObject):
    errorOccurred = Signal(str)
    addonsChanged = Signal()
    tmdbKeyChanged = Signal()
    subtitleStyleChanged = Signal()

    def __init__(
        self,
        uninstall: UninstallAddon,
        repo: AddonRepository,
        model: AddonListModel,
        catalog_controller: _RefreshesCatalog,
        key_holder: _KeyHolder | None = None,
        store: SettingsStore | None = None,
        style_holder: _StyleHolder | None = None,
    ) -> None:
        super().__init__()
        self._uninstall = uninstall
        self._repo = repo
        self._model = model
        self._catalog_controller = catalog_controller
        self._key_holder = key_holder
        self._store = store
        self._style_holder = style_holder

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
        """Write the current user state to the store."""
        if self._store is None:
            return
        key = self._key_holder.key if self._key_holder is not None else None
        style = self._style_holder.style if self._style_holder is not None else SubtitleStyle()
        self._store.save(
            PersistedSettings(
                addon_urls=tuple(self._repo.user_addon_urls()),
                tmdb_key=key,
                subtitle_style=style,
            )
        )

    # --- subtitle style ---

    def _style(self) -> SubtitleStyle:
        return self._style_holder.style if self._style_holder is not None else SubtitleStyle()

    def _update_style(self, **changes: object) -> None:
        if self._style_holder is None:
            return
        self._style_holder.style = replace(self._style_holder.style, **changes)  # type: ignore[arg-type]
        self.subtitleStyleChanged.emit()
        self.persist()

    @Property(int, notify=subtitleStyleChanged)
    def subFontSize(self) -> int:
        return self._style().font_size

    @Property(str, notify=subtitleStyleChanged)
    def subColor(self) -> str:
        return self._style().color

    @Property(int, notify=subtitleStyleChanged)
    def subBorderSize(self) -> int:
        return self._style().border_size

    @Property(int, notify=subtitleStyleChanged)
    def subBackOpacity(self) -> int:
        return self._style().back_opacity

    @Property(bool, notify=subtitleStyleChanged)
    def subBold(self) -> bool:
        return self._style().bold

    @Slot(int)
    def setSubFontSize(self, size: int) -> None:
        self._update_style(font_size=max(20, min(100, size)))

    @Slot(str)
    def setSubColor(self, color: str) -> None:
        self._update_style(color=color)

    @Slot(int)
    def setSubBorderSize(self, size: int) -> None:
        self._update_style(border_size=max(0, min(8, size)))

    @Slot(int)
    def setSubBackOpacity(self, opacity: int) -> None:
        self._update_style(back_opacity=max(0, min(100, opacity)))

    @Slot(bool)
    def setSubBold(self, bold: bool) -> None:
        self._update_style(bold=bold)

    @Slot()
    def resetSubtitleStyle(self) -> None:
        if self._style_holder is None:
            return
        self._style_holder.style = SubtitleStyle()
        self.subtitleStyleChanged.emit()
        self.persist()

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
