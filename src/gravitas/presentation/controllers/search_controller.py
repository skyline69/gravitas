"""QObject bridge: debounced search + link resolution feeding SearchResultsModel."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.media_links import parse_media_link
from gravitas.application.search_ranking import rank
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.search_results_model import SearchResultsModel


class _Search(Protocol):
    async def __call__(self, query: str) -> list[MediaItem]: ...


class _SearchStream(Protocol):
    def __call__(self, query: str) -> AsyncIterator[list[MediaItem]]: ...


class _Resolve(Protocol):
    async def __call__(self, source: str, external_id: str) -> MediaItem: ...


class SearchController(QObject):
    resultsChanged = Signal()
    loadingChanged = Signal(bool)
    errorOccurred = Signal(str)

    def __init__(
        self,
        search: _Search,
        resolve: _Resolve,
        model: SearchResultsModel,
        page_model: SearchResultsModel | None = None,
        search_stream: _SearchStream | None = None,
    ) -> None:
        super().__init__()
        self._search = search
        self._resolve = resolve
        self._model = model
        self._page_model = page_model
        self._search_stream = search_stream
        self._cache: dict[str, list[MediaItem]] = {}
        self._pending = ""
        self._req = 0
        self._last_items: list[MediaItem] = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(180)
        self._timer.timeout.connect(self._fire)

    @Slot(str)
    def queueSearch(self, text: str) -> None:
        self._pending = text.strip()
        if not self._pending:
            self._reset_state()
            return
        self._timer.start()

    @Slot()
    def clear(self) -> None:
        self._pending = ""
        self._reset_state()

    def _reset_state(self) -> None:
        # Empty query / clear: cancel the timer, invalidate any in-flight
        # request (bump _req), empty the model, and force loading off — the
        # superseded request's own `loadingChanged(False)` is suppressed by the
        # stale guard, so it must be emitted here or the spinner sticks.
        self._timer.stop()
        self._req += 1
        self._last_items = []
        self._model.set_items([])
        self.resultsChanged.emit()
        self.loadingChanged.emit(False)

    @Slot()
    def commitToPage(self) -> None:
        if self._page_model is not None:
            self._page_model.set_items(self._last_items)

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
                self._commit([await self._resolve(link[0], link[1])], req)
                return
            key = text.lower()
            cached = self._cache.get(key)
            if cached is not None:
                self._commit(cached, req)  # instant on repeats
                return
            if self._search_stream is not None:
                # Stream: show each addon's results the moment they arrive,
                # re-ranked by relevance as the accumulated set grows.
                acc: list[MediaItem] = []
                async for batch in self._search_stream(text):
                    if req != self._req:
                        return
                    acc = acc + batch
                    self._commit(rank(text, acc), req)
                self._store_cache(key, rank(text, acc))
            else:
                items = rank(text, await self._search(text))
                self._store_cache(key, items)
                self._commit(items, req)
        except GravitasError as exc:
            if req == self._req:
                self.errorOccurred.emit(str(exc))
        finally:
            if req == self._req:
                self.loadingChanged.emit(False)

    def _commit(self, items: list[MediaItem], req: int) -> None:
        if req == self._req:
            self._last_items = items
            self._model.set_items(items)
            self.resultsChanged.emit()

    def _store_cache(self, key: str, items: list[MediaItem]) -> None:
        self._cache[key] = items
        if len(self._cache) > 128:
            self._cache.pop(next(iter(self._cache)))
