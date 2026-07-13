"""QObject bridge: debounced search + link resolution feeding SearchResultsModel."""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.media_links import parse_media_link
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.search_results_model import SearchResultsModel


class _Search(Protocol):
    async def __call__(self, query: str) -> list[MediaItem]: ...


class _Resolve(Protocol):
    async def __call__(self, source: str, external_id: str) -> MediaItem: ...


class SearchController(QObject):
    resultsChanged = Signal()
    loadingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(self, search: _Search, resolve: _Resolve, model: SearchResultsModel) -> None:
        super().__init__()
        self._search = search
        self._resolve = resolve
        self._model = model
        self._pending = ""
        self._req = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._fire)

    @Slot(str)
    def queueSearch(self, text: str) -> None:
        self._pending = text.strip()
        if not self._pending:
            self._timer.stop()
            self._model.set_items([])
            self.resultsChanged.emit()
            return
        self._timer.start()

    @Slot()
    def clear(self) -> None:
        self._timer.stop()
        self._pending = ""
        self._model.set_items([])
        self.resultsChanged.emit()

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def _fire(self) -> None:
        await self._perform(self._pending)

    async def _perform(self, text: str) -> None:
        self._req += 1
        await self._perform_with_id(text, self._req)

    async def _perform_with_id(self, text: str, req: int) -> None:
        self.loadingChanged.emit(True)
        try:
            link = parse_media_link(text)
            if link is not None:
                items = [await self._resolve(link[0], link[1])]
            else:
                items = await self._search(text)
        except GravitasError as exc:
            if req == self._req:
                self.errorOccurred.emit(str(exc))
            return
        finally:
            if req == self._req:
                self.loadingChanged.emit(False)
        if req == self._req:
            self._model.set_items(items)
            self.resultsChanged.emit()
