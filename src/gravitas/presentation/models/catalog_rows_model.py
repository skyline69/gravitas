"""Qt list model exposing catalog rows, each with its own poster model, to QML."""

from __future__ import annotations

from typing import Any, NamedTuple

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.application.browse_catalog import CatalogRow
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaItem, PlaybackProgress
from gravitas.presentation.models.poster_grid_model import PosterGridModel

_ROOT_INDEX = QModelIndex()

CONTINUE_WATCHING_TITLE = "Continue Watching"


class _Row(NamedTuple):
    """A row as QML sees it. The PosterGridModel reference is held here to keep
    it alive for the QML binding."""

    title: str
    addon_id: str
    type: str
    catalog_id: str
    posters: PosterGridModel
    continue_watching: bool = False


class CatalogRowsModel(QAbstractListModel):
    TitleRole = Qt.ItemDataRole.UserRole + 1
    AddonIdRole = Qt.ItemDataRole.UserRole + 2
    TypeRole = Qt.ItemDataRole.UserRole + 3
    CatalogIdRole = Qt.ItemDataRole.UserRole + 4
    PostersRole = Qt.ItemDataRole.UserRole + 5
    ContinueWatchingRole = Qt.ItemDataRole.UserRole + 6

    def __init__(self, progress: WatchProgressRepository | None = None) -> None:
        super().__init__()
        self._progress = progress
        # Catalog rows only, and the currently-visible subset (which may also
        # carry the synthetic Continue Watching row).
        self._all_rows: list[_Row] = []
        self._rows: list[_Row] = []
        self._filter = "all"
        # Continue Watching is held apart from the catalog rows because it is
        # rebuilt on a different clock: progress changes every few seconds
        # during playback, and folding it into set_rows() would make each
        # rebuild re-fetch every addon's /catalog over the network.
        self._cw_entries: list[PlaybackProgress] = []
        self._cw_row: _Row | None = None

    # --- rows ---

    def set_rows(self, rows: list[CatalogRow]) -> None:
        self.beginResetModel()
        self._all_rows = [
            _Row(
                title=row.title,
                addon_id=row.addon_id,
                type=row.type,
                catalog_id=row.catalog_id,
                posters=self._poster_model(row.items),
                continue_watching=False,
            )
            for row in rows
        ]
        self._rebuild()
        self.endResetModel()

    def set_continue_watching(self, entries: list[PlaybackProgress]) -> None:
        """Replace the Continue Watching row. Cheap and network-free: every
        field a card needs is already denormalized onto the progress entry."""
        self.beginResetModel()
        self._cw_entries = list(entries)
        self._rebuild()
        self.endResetModel()

    def set_filter(self, mode: str) -> None:
        self.beginResetModel()
        self._filter = mode
        self._rebuild()
        self.endResetModel()

    def refresh_progress(self) -> None:
        # The nested poster models own the visible cells; refresh every row's,
        # not just the filtered subset — a filter switch must not show stale bars.
        for row in self._all_rows:
            row.posters.refresh_progress()
        if self._cw_row is not None:
            self._cw_row.posters.refresh_progress()

    # --- assembly ---

    def _poster_model(self, items: list[MediaItem]) -> PosterGridModel:
        model = PosterGridModel(self._progress)
        model.set_items(items)
        return model

    def _rebuild(self) -> None:
        """Recompute the visible rows. Catalog rows keep their existing poster
        models — only the Continue Watching row is rebuilt, since its contents
        depend on the active tab."""
        self._cw_row = self._build_continue_watching()
        rows = self._filtered(self._all_rows, self._filter)
        self._rows = ([self._cw_row] if self._cw_row is not None else []) + rows

    def _build_continue_watching(self) -> _Row | None:
        if self._filter == "trending":
            # Trending is what is popular, not what you personally started.
            return None
        entries = self._cw_entries
        if self._filter in ("movie", "series"):
            entries = [e for e in entries if e.type == self._filter]
        if not entries:
            # An empty row is worse than no row.
            return None
        items = [
            MediaItem(id=e.media_id, type=e.type, name=e.name, poster=e.poster) for e in entries
        ]
        return _Row(
            title=CONTINUE_WATCHING_TITLE,
            # No addon and no catalog stand behind this row, so See All has
            # nowhere to go — QML hides it on the continueWatching flag.
            addon_id="",
            type="",
            catalog_id="",
            posters=self._poster_model(items),
            continue_watching=True,
        )

    @staticmethod
    def _filtered(rows: list[_Row], mode: str) -> list[_Row]:
        if mode in ("movie", "series"):
            return [r for r in rows if r.type == mode]
        if mode == "trending":
            keywords = ("top", "trending", "popular")
            return [
                r
                for r in rows
                if any(k in r.title.lower() or k in r.catalog_id.lower() for k in keywords)
            ]
        return list(rows)

    # --- QML interface ---

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        match role:
            case CatalogRowsModel.TitleRole:
                return row.title
            case CatalogRowsModel.AddonIdRole:
                return row.addon_id
            case CatalogRowsModel.TypeRole:
                return row.type
            case CatalogRowsModel.CatalogIdRole:
                return row.catalog_id
            case CatalogRowsModel.PostersRole:
                return row.posters
            case CatalogRowsModel.ContinueWatchingRole:
                return row.continue_watching
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            CatalogRowsModel.TitleRole: QByteArray(b"title"),
            CatalogRowsModel.AddonIdRole: QByteArray(b"addonId"),
            CatalogRowsModel.TypeRole: QByteArray(b"type"),
            CatalogRowsModel.CatalogIdRole: QByteArray(b"catalogId"),
            CatalogRowsModel.PostersRole: QByteArray(b"posters"),
            CatalogRowsModel.ContinueWatchingRole: QByteArray(b"continueWatching"),
        }
