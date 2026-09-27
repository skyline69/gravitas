"""QObject bridge: run BrowseCatalog and populate the catalog rows model."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel


class CatalogController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)
    bootingChanged = Signal()

    def __init__(self, browse: BrowseCatalog, model: CatalogRowsModel) -> None:
        super().__init__()
        self._browse = browse
        self._model = model
        self._booting = False

    @Property(bool, notify=bootingChanged)
    def booting(self) -> bool:
        """True from launch until the whole first load — addons, catalog,
        Continue Watching, Trakt — has landed. Home holds a full-page spinner
        on this so the grid appears once, complete, instead of assembling
        itself row by row in front of the user."""
        return self._booting

    def set_booting(self, value: bool) -> None:
        if self._booting == value:
            return
        self._booting = value
        self.bootingChanged.emit()

    async def load_catalog(self, *, quiet: bool = False) -> None:
        """Load rows into the model. `quiet` skips the loading signal — the
        boot flow owns its own indicator, and its background revalidation
        pass must not flash a spinner over content already on screen."""
        if not quiet:
            self.loadingChanged.emit(True)
        try:
            rows = await self._browse()
            self._model.set_rows(rows)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            if not quiet:
                self.loadingChanged.emit(False)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def refresh(self) -> None:
        await self.load_catalog()

    @Slot(str)
    def setFilter(self, mode: str) -> None:
        self._model.set_filter(mode)
