"""Qt list model for search results (poster + title + year), used by the
dropdown preview and the full results grid."""

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


class SearchResultsModel(QAbstractListModel):
    IdRole = Qt.ItemDataRole.UserRole + 1
    TypeRole = Qt.ItemDataRole.UserRole + 2
    NameRole = Qt.ItemDataRole.UserRole + 3
    PosterRole = Qt.ItemDataRole.UserRole + 4
    YearRole = Qt.ItemDataRole.UserRole + 5

    def __init__(self) -> None:
        super().__init__()
        self._items: list[MediaItem] = []

    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

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
            case SearchResultsModel.IdRole:
                return item.id
            case SearchResultsModel.TypeRole:
                return item.type
            case SearchResultsModel.NameRole:
                return item.name
            case SearchResultsModel.PosterRole:
                return item.poster or ""
            case SearchResultsModel.YearRole:
                return item.year or ""
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            SearchResultsModel.IdRole: QByteArray(b"mediaId"),
            SearchResultsModel.TypeRole: QByteArray(b"type"),
            SearchResultsModel.NameRole: QByteArray(b"name"),
            SearchResultsModel.PosterRole: QByteArray(b"poster"),
            SearchResultsModel.YearRole: QByteArray(b"year"),
        }
