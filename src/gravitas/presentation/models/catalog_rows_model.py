"""Qt list model exposing catalog rows, each with its own poster model, to QML."""

from __future__ import annotations

import difflib
from typing import Any, NamedTuple

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.application.browse_catalog import CatalogRow
from gravitas.application.trakt_rows import TraktRow
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaItem, PlaybackProgress
from gravitas.presentation.models.poster_grid_model import PosterGridModel

_ROOT_INDEX = QModelIndex()

CONTINUE_WATCHING_TITLE = "Continue Watching"


def _row_key(row: CatalogRow) -> tuple[str, str, str, str]:
    """A catalog row's identity (everything but its items)."""
    return (row.title, row.addon_id, row.type, row.catalog_id)


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
        # carry the synthetic Continue Watching row). _source_rows is the
        # as-given input, kept to recognize a no-op refresh.
        self._source_rows: list[CatalogRow] = []
        self._all_rows: list[_Row] = []
        self._rows: list[_Row] = []
        self._filter = "all"
        # Trakt-served rows (recommendations, history) — like catalog rows
        # but with no addon behind them, and set on their own clock: a slow
        # Trakt answer lands after the catalog painted. _trakt_source_rows is
        # the as-given input, kept to recognize a no-op refresh.
        self._trakt_rows: list[_Row] = []
        self._trakt_source_rows: list[TraktRow] = []
        # Continue Watching is held apart from the catalog rows because it is
        # rebuilt on a different clock: progress changes every few seconds
        # during playback, and folding it into set_rows() would make each
        # rebuild re-fetch every addon's /catalog over the network.
        self._cw_entries: list[PlaybackProgress] = []
        self._cw_row: _Row | None = None
        # What the current CW row displays — the change detector that lets
        # the 5s playback tick skip rebuilding an unchanged row.
        self._cw_shown_items: list[MediaItem] = []

    # --- rows ---

    def set_rows(self, rows: list[CatalogRow]) -> None:
        if rows == self._source_rows:
            # A refresh pass re-derived identical content (the common case for
            # the boot's revalidation pass over warm caches) — a reset here
            # would tear down and re-incubate every delegate to show the same
            # thing.
            return
        if [_row_key(r) for r in rows] == [_row_key(r) for r in self._source_rows]:
            # Same rows, some with different items (a catalog shuffled): feed
            # the changed strips' poster models in place. Only those strips
            # rebuild their cards; every other delegate on the page survives.
            for new, old, shown in zip(rows, self._source_rows, self._all_rows, strict=True):
                if new.items != old.items:
                    shown.posters.set_items(new.items)
            self._source_rows = list(rows)
            return
        self._source_rows = list(rows)
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

    def set_trakt_rows(self, rows: list[TraktRow]) -> None:
        """Replace the Trakt rows surgically — remove the old block, insert
        the new one — never with a model reset. The Trakt rows land AFTER the
        catalog painted (startup, sync, auth), and a reset at that moment
        would tear down and re-incubate every catalog row's delegates just to
        splice a few rows in above them."""
        if rows == self._trakt_source_rows:
            # The refresh confirmed what the boot snapshot already showed —
            # the common warm-start case. Splicing identical rows back in
            # would rebuild their delegates and re-trickle every poster: the
            # page would visibly "load again" seconds after it appeared.
            return
        if [(r.title, r.type) for r in rows] == [
            (r.title, r.type) for r in self._trakt_source_rows
        ]:
            # Same rows, some content moved: feed the changed strips' poster
            # models in place, keep every delegate.
            for new, old, shown in zip(
                rows, self._trakt_source_rows, self._trakt_rows, strict=True
            ):
                if new.items != old.items:
                    shown.posters.set_items(new.items)
            self._trakt_source_rows = list(rows)
            return
        self._trakt_source_rows = list(rows)
        fresh = [
            _Row(
                title=row.title,
                # No addon and no catalog stand behind these rows; QML hides
                # See All when there is no catalog to see all of.
                addon_id="",
                type=row.type,
                catalog_id="",
                posters=self._poster_model(row.items),
            )
            for row in rows
        ]
        # The visible Trakt block sits contiguously between the (optional)
        # Continue Watching row and the catalog rows.
        start = 1 if self._cw_row is not None else 0
        old_count = len(self._filtered_trakt())
        if old_count:
            self.beginRemoveRows(_ROOT_INDEX, start, start + old_count - 1)
            del self._rows[start : start + old_count]
            self._trakt_rows = []
            self.endRemoveRows()
        self._trakt_rows = fresh
        visible = self._filtered_trakt()
        if visible:
            self.beginInsertRows(_ROOT_INDEX, start, start + len(visible) - 1)
            self._rows[start:start] = visible
            self.endInsertRows()

    def set_continue_watching(self, entries: list[PlaybackProgress]) -> None:
        """Replace the Continue Watching row. Network-free: every field a card
        needs is already denormalized onto the progress entry.

        This runs on the 5s playback tick, so it must NOT reset the model. A
        reset tears down and rebuilds every catalog row's delegates -- aborting
        their in-flight poster fetches (`QSslSocket: device not open`) and
        half-incubated cards (`object destroyed during incubation`) -- just to
        touch the single synthetic row that lives at index 0. Mutate only that
        row instead; the catalog delegates never see it.

        Rebuild only when the row's CONTENT changed. The tick's usual payload
        is a moved resume position, which reaches the cards through
        refresh_progress() (model roles), not through this row's item list —
        rebuilding then would tear down and re-incubate the row's delegates
        every 5 seconds during playback for no visible change.
        """
        self._cw_entries = list(entries)
        if self._cw_row is not None and self._cw_visible_items() == self._cw_shown_items:
            return
        old_row, new_row = self._cw_row, self._build_continue_watching()
        self._cw_row = new_row
        if old_row is not None and new_row is not None:
            self._rows[0] = new_row
            top = self.index(0)
            self.dataChanged.emit(top, top)
        elif new_row is not None:
            self.beginInsertRows(_ROOT_INDEX, 0, 0)
            self._rows.insert(0, new_row)
            self.endInsertRows()
        elif old_row is not None:
            self.beginRemoveRows(_ROOT_INDEX, 0, 0)
            del self._rows[0]
            self.endRemoveRows()

    def set_filter(self, mode: str) -> None:
        """Switch the visible subset surgically. A reset here tore down and
        re-incubated EVERY strip on the page — each tab switch flashed a full
        screen of poster skeletons. Rows visible under both filters are the
        same _Row objects (subsets of _all_rows / _trakt_rows), so diffing
        old->new yields only the rows that actually enter or leave; their
        delegates and decoded posters survive untouched."""
        if mode == self._filter:
            return
        self._filter = mode
        self._cw_row = self._current_cw_row()
        rows = self._filtered(self._all_rows, self._filter)
        trakt = self._filtered_trakt()
        new = ([self._cw_row] if self._cw_row is not None else []) + trakt + rows
        self._apply_visible(new)

    def _current_cw_row(self) -> _Row | None:
        """The Continue Watching row for the active filter — reusing the live
        row (and its delegate) when the filter change didn't alter which cards
        it shows."""
        if self._cw_row is not None and self._cw_visible_items() == self._cw_shown_items:
            return self._cw_row
        return self._build_continue_watching()

    def _apply_visible(self, new_rows: list[_Row]) -> None:
        """Morph _rows into new_rows through granular remove/insert ranges.
        Both lists order rows the same way (CW, Trakt block, catalog), so the
        diff is clean subsequence surgery. Opcodes are applied back-to-front
        so earlier ranges' indices stay valid."""
        matcher = difflib.SequenceMatcher(a=self._rows, b=new_rows, autojunk=False)
        for tag, i1, i2, j1, j2 in reversed(matcher.get_opcodes()):
            if tag in ("replace", "delete"):
                self.beginRemoveRows(_ROOT_INDEX, i1, i2 - 1)
                del self._rows[i1:i2]
                self.endRemoveRows()
            if tag in ("replace", "insert"):
                self.beginInsertRows(_ROOT_INDEX, i1, i1 + (j2 - j1) - 1)
                self._rows[i1:i1] = new_rows[j1:j2]
                self.endInsertRows()

    def refresh_progress(self) -> None:
        # The nested poster models own the visible cells; refresh every row's,
        # not just the filtered subset — a filter switch must not show stale bars.
        for row in self._all_rows:
            row.posters.refresh_progress()
        for row in self._trakt_rows:
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
        trakt = self._filtered_trakt()
        self._rows = ([self._cw_row] if self._cw_row is not None else []) + trakt + rows

    def _cw_visible_items(self) -> list[MediaItem]:
        """The card list the Continue Watching row would show under the
        active filter — the identity set_continue_watching compares to decide
        whether a rebuild is warranted."""
        if self._filter == "trending":
            # Trending is what is popular, not what you personally started.
            return []
        entries = self._cw_entries
        if self._filter in ("movie", "series"):
            entries = [e for e in entries if e.type == self._filter]
        return [
            MediaItem(id=e.media_id, type=e.type, name=e.name, poster=e.poster) for e in entries
        ]

    def _build_continue_watching(self) -> _Row | None:
        items = self._cw_shown_items = self._cw_visible_items()
        if not items:
            # An empty row is worse than no row.
            return None
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

    def _filtered_trakt(self) -> list[_Row]:
        """Trakt rows under the active filter. Personalized rows are not
        trending; the mixed-type history row (type "") shows only under All."""
        if self._filter in ("movie", "series"):
            return [r for r in self._trakt_rows if r.type == self._filter]
        if self._filter == "trending":
            return []
        return list(self._trakt_rows)

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
