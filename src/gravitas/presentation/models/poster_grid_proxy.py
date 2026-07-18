"""Client-side filter/sort proxy over a PosterGridModel.

Purely a presentation concern: Stremio catalogs arrive pre-sorted from the
addon, so this proxy only narrows and reorders the items already loaded into
its source model. Pagination (loadMore) keeps appending to the source; with
dynamicSortFilter on (the QSortFilterProxyModel default) appended rows are
filtered and inserted in sorted position automatically.
"""

from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import (
    Property,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
    Slot,
)

from gravitas.presentation.models.poster_grid_model import PosterGridModel

SORT_KEYS = ("default", "name", "year", "rating")

_YEAR_RE = re.compile(r"\d{4}")


@lru_cache(maxsize=4096)
def _year_of(value: str) -> float | None:
    match = _YEAR_RE.search(value)
    return float(match.group()) if match else None


def _first_year(value: object) -> float | None:
    """Parse '1999' or '2010-2015' style year strings to a sortable number.

    A comparison sort calls this O(n log n) times over the same handful of
    distinct year strings, so the regex result is memoised by input value.
    """
    return _year_of(value) if isinstance(value, str) else None


_RATING_RE = re.compile(r"\d+(?:[.,]\d+)?")


@lru_cache(maxsize=4096)
def _rating_of(value: str) -> float | None:
    match = _RATING_RE.search(value)
    if match is None:
        return None
    return float(match.group().replace(",", "."))


def _rating(value: object) -> float | None:
    """Parse '8.1', '8,1', '8.1/10', 'IMDb 8.1' — addons format ratings loosely.

    Memoised like _first_year: the same rating strings recur across every
    pairwise comparison in a sort.
    """
    return _rating_of(value) if isinstance(value, str) else None


class PosterGridProxy(QSortFilterProxyModel):
    countChanged = Signal()

    def __init__(self, source: PosterGridModel) -> None:
        super().__init__()
        self._sort_key = "default"
        self.setSourceModel(source)
        self.setFilterRole(PosterGridModel.NameRole)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # Filtering/appending changes the visible row count through several
        # different model signals; funnel them all into one QML-friendly
        # notify so the "N titles" label stays live.
        self.rowsInserted.connect(self.countChanged)
        self.rowsRemoved.connect(self.countChanged)
        self.modelReset.connect(self.countChanged)
        self.layoutChanged.connect(self.countChanged)
        source.rowsInserted.connect(self.countChanged)
        source.rowsRemoved.connect(self.countChanged)
        source.modelReset.connect(self.countChanged)

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self.rowCount()

    @Property(int, notify=countChanged)
    def totalCount(self) -> int:
        source = self.sourceModel()
        return source.rowCount() if source is not None else 0

    @Slot(str)
    def setFilterText(self, text: str) -> None:
        self.setFilterFixedString(text)

    @Slot(str)
    def setSortKey(self, key: str) -> None:
        if key not in SORT_KEYS or key == self._sort_key:
            return
        self._sort_key = key
        # sort(-1) drops the proxy back to source order; re-sorting on column 0
        # afterwards re-runs lessThan under the new key.
        self.sort(-1)
        if key != "default":
            self.sort(0)

    def lessThan(
        self,
        source_left: QModelIndex | QPersistentModelIndex,
        source_right: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        source = self.sourceModel()
        if self._sort_key == "name":
            left = str(source.data(source_left, PosterGridModel.NameRole) or "")
            right = str(source.data(source_right, PosterGridModel.NameRole) or "")
            return left.casefold() < right.casefold()
        if self._sort_key == "year":
            return self._descending(source_left, source_right, PosterGridModel.YearRole)
        if self._sort_key == "rating":
            return self._descending(source_left, source_right, PosterGridModel.RatingRole)
        return source_left.row() < source_right.row()

    def _descending(
        self,
        source_left: QModelIndex | QPersistentModelIndex,
        source_right: QModelIndex | QPersistentModelIndex,
        role: int,
    ) -> bool:
        """Highest first; items missing the value sort last; ties keep source order."""
        source = self.sourceModel()
        parse = _first_year if role == PosterGridModel.YearRole else _rating
        left = parse(source.data(source_left, role))
        right = parse(source.data(source_right, role))
        if left is None and right is None:
            return source_left.row() < source_right.row()
        if left is None:
            return False
        if right is None:
            return True
        if left == right:
            return source_left.row() < source_right.row()
        return left > right
