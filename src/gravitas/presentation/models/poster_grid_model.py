"""Qt list model exposing MediaItems to the QML poster grid."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import MediaItem

_ROOT_INDEX = QModelIndex()


class PosterGridModel(QAbstractListModel):
    IdRole = Qt.ItemDataRole.UserRole + 1
    TypeRole = Qt.ItemDataRole.UserRole + 2
    NameRole = Qt.ItemDataRole.UserRole + 3
    PosterRole = Qt.ItemDataRole.UserRole + 4
    YearRole = Qt.ItemDataRole.UserRole + 5
    RatingRole = Qt.ItemDataRole.UserRole + 6

    def __init__(self) -> None:
        super().__init__()
        self._items: list[MediaItem] = []

    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def append_items(self, items: list[MediaItem]) -> int:
        """Append items not already present (by id); returns how many were new.

        Addons routinely repeat items across pagination pages (or ignore skip
        entirely) — blind appends accumulate visible duplicates.
        """
        seen = {item.id for item in self._items}
        fresh: list[MediaItem] = []
        for item in items:
            if item.id not in seen:
                seen.add(item.id)
                fresh.append(item)
        if not fresh:
            return 0
        start = len(self._items)
        self.beginInsertRows(_ROOT_INDEX, start, start + len(fresh) - 1)
        self._items.extend(fresh)
        self.endInsertRows()
        return len(fresh)

    def item_at(self, row: int) -> MediaItem:
        return self._items[row]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._items)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        item = self._items[index.row()]
        match role:
            case PosterGridModel.IdRole:
                return item.id
            case PosterGridModel.TypeRole:
                return item.type
            case PosterGridModel.NameRole:
                return item.name
            case PosterGridModel.PosterRole:
                return item.poster
            case PosterGridModel.YearRole:
                return item.year
            case PosterGridModel.RatingRole:
                return item.imdb_rating
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            PosterGridModel.IdRole: QByteArray(b"id"),
            PosterGridModel.TypeRole: QByteArray(b"type"),
            PosterGridModel.NameRole: QByteArray(b"name"),
            PosterGridModel.PosterRole: QByteArray(b"poster"),
            PosterGridModel.YearRole: QByteArray(b"year"),
            PosterGridModel.RatingRole: QByteArray(b"rating"),
        }
