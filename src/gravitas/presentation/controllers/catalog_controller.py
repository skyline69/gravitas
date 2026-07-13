"""QObject bridge: run BrowseCatalog and populate the catalog rows model."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel


class CatalogController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)

    def __init__(self, browse: BrowseCatalog, model: CatalogRowsModel) -> None:
        super().__init__()
        self._browse = browse
        self._model = model

    async def load_catalog(self) -> None:
        self.loadingChanged.emit(True)
        try:
            rows = await self._browse()
            self._model.set_rows(rows)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            self.loadingChanged.emit(False)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def refresh(self) -> None:
        await self.load_catalog()

    @Slot(str)
    def setFilter(self, mode: str) -> None:
        self._model.set_filter(mode)
