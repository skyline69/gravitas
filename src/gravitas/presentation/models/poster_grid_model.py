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

    def __init__(self) -> None:
        super().__init__()
        self._items: list[MediaItem] = []

    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def append_items(self, items: list[MediaItem]) -> None:
        if not items:
            return
        start = len(self._items)
        self.beginInsertRows(_ROOT_INDEX, start, start + len(items) - 1)
        self._items.extend(items)
        self.endInsertRows()

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
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            PosterGridModel.IdRole: QByteArray(b"id"),
            PosterGridModel.TypeRole: QByteArray(b"type"),
            PosterGridModel.NameRole: QByteArray(b"name"),
            PosterGridModel.PosterRole: QByteArray(b"poster"),
        }
