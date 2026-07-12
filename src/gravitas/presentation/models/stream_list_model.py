"""Qt list model exposing resolved streams to the QML source picker."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import Stream

_ROOT_INDEX = QModelIndex()


class StreamListModel(QAbstractListModel):
    NameRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    UrlRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self) -> None:
        super().__init__()
        self._streams: list[Stream] = []

    def set_streams(self, streams: list[Stream]) -> None:
        self.beginResetModel()
        self._streams = list(streams)
        self.endResetModel()

    def stream_at(self, row: int) -> Stream:
        return self._streams[row]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._streams)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        stream = self._streams[index.row()]
        match role:
            case StreamListModel.NameRole:
                return stream.name
            case StreamListModel.TitleRole:
                return stream.title
            case StreamListModel.UrlRole:
                return stream.url
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            StreamListModel.NameRole: QByteArray(b"name"),
            StreamListModel.TitleRole: QByteArray(b"title"),
            StreamListModel.UrlRole: QByteArray(b"url"),
        }
