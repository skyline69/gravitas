"""QObject bridge: drive the Discover board (filters + paginated grid)."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository, CatalogOption
from gravitas.application.browse_board import BrowseBoard
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import MediaType
from gravitas.presentation.models.poster_grid_model import PosterGridModel


class DiscoverController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)
    optionsChanged = Signal()

    def __init__(self, browse: BrowseBoard, repo: AddonRepository, model: PosterGridModel) -> None:
        super().__init__()
        self._browse = browse
        self._repo = repo
        self._model = model
        self._options: list[CatalogOption] = []
        self._type: MediaType = "movie"
        self._catalog_idx = 0
        self._genre: str | None = None
        self._skip = 0
        self._has_more = False
        self._loading = False

    def _types(self) -> list[str]:
        seen: list[str] = []
        for opt in self._options:
            if opt.type not in seen:
                seen.append(opt.type)
        return seen

    def _catalogs_for_type(self) -> list[CatalogOption]:
        return [opt for opt in self._options if opt.type == self._type]

    def _genre_options(self) -> list[str]:
        cats = self._catalogs_for_type()
        if not cats or self._catalog_idx >= len(cats):
            return ["All"]
        return ["All", *cats[self._catalog_idx].genres]

    @Property("QVariantList", notify=optionsChanged)  # type: ignore[arg-type]
    def typeOptions(self) -> list[str]:
        return self._types()

    @Property("QVariantList", notify=optionsChanged)  # type: ignore[arg-type]
    def catalogOptions(self) -> list[str]:
        return [opt.label for opt in self._catalogs_for_type()]

    @Property("QVariantList", notify=optionsChanged)  # type: ignore[arg-type]
    def genreOptions(self) -> list[str]:
        return self._genre_options()

    @Property(int, notify=optionsChanged)
    def typeIndex(self) -> int:
        types = self._types()
        return types.index(self._type) if self._type in types else 0

    @Property(int, notify=optionsChanged)
    def catalogIndex(self) -> int:
        return self._catalog_idx

    @Property(int, notify=optionsChanged)
    def genreIndex(self) -> int:
        if self._genre is None:
            return 0
        genres = self._genre_options()
        return genres.index(self._genre) if self._genre in genres else 0

    @asyncSlot(str, str, str)  # type: ignore[untyped-decorator]
    async def open(self, addon_id: str, type: str, catalog_id: str) -> None:
        self._options = self._repo.catalog_options()
        self._type = "series" if type == "series" else "movie"
        cats = self._catalogs_for_type()
        self._catalog_idx = next(
            (
                i
                for i, opt in enumerate(cats)
                if opt.addon_id == addon_id and opt.catalog_id == catalog_id
            ),
            0,
        )
        self._genre = None
        self.optionsChanged.emit()
        await self._reload()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def selectType(self, index: int) -> None:
        types = self._types()
        if not 0 <= index < len(types):
            return
        self._type = "series" if types[index] == "series" else "movie"
        self._catalog_idx = 0
        self._genre = None
        self.optionsChanged.emit()
        await self._reload()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def selectCatalog(self, index: int) -> None:
        if not 0 <= index < len(self._catalogs_for_type()):
            return
        self._catalog_idx = index
        self._genre = None
        self.optionsChanged.emit()
        await self._reload()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def selectGenre(self, index: int) -> None:
        genres = self._genre_options()
        if not 0 <= index < len(genres):
            return
        self._genre = None if index == 0 else genres[index]
        await self._reload()

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def loadMore(self) -> None:
        if self._loading or not self._has_more:
            return
        cats = self._catalogs_for_type()
        if not cats:
            return
        self._skip += BrowseBoard.PAGE_SIZE
        await self._fetch(cats[self._catalog_idx], append=True)

    async def _reload(self) -> None:
        self._skip = 0
        cats = self._catalogs_for_type()
        if not cats:
            self._model.set_items([])
            self._has_more = False
            return
        await self._fetch(cats[self._catalog_idx], append=False)

    async def _fetch(self, cat: CatalogOption, *, append: bool) -> None:
        self._loading = True
        self.loadingChanged.emit(True)
        try:
            page = await self._browse(
                cat.addon_id, cat.type, cat.catalog_id, genre=self._genre, skip=self._skip
            )
            if append:
                self._model.append_items(page.items)
            else:
                self._model.set_items(page.items)
            self._has_more = page.has_more
        except GravitasError as exc:
            self._has_more = False
            self.errorOccurred.emit(str(exc))
        finally:
            self._loading = False
            self.loadingChanged.emit(False)
