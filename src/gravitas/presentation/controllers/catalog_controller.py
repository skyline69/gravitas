"""QObject bridge: run BrowseCatalog and populate the poster grid model."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.poster_grid_model import PosterGridModel


class CatalogController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)

    def __init__(self, browse: BrowseCatalog, model: PosterGridModel) -> None:
        super().__init__()
        self._browse = browse
        self._model = model

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def refresh(self) -> None:
        self.loadingChanged.emit(True)
        try:
            rows = await self._browse()
            items = [item for row in rows for item in row.items]
            self._model.set_items(items)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            self.loadingChanged.emit(False)
