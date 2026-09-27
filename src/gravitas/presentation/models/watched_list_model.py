"""Qt list model exposing in-progress titles to the Settings page.

One row per media (the latest episode for a series), newest first — the list
is a management surface, not a history log.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import PlaybackProgress

_ROOT_INDEX = QModelIndex()


class WatchedListModel(QAbstractListModel):
    MediaIdRole = Qt.ItemDataRole.UserRole + 1
    VideoIdRole = Qt.ItemDataRole.UserRole + 2
    TypeRole = Qt.ItemDataRole.UserRole + 3
    NameRole = Qt.ItemDataRole.UserRole + 4
    PosterRole = Qt.ItemDataRole.UserRole + 5
    LabelRole = Qt.ItemDataRole.UserRole + 6
    ProgressFractionRole = Qt.ItemDataRole.UserRole + 7

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[PlaybackProgress] = []

    def set_entries(self, entries: list[PlaybackProgress]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._entries)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        match role:
            case WatchedListModel.MediaIdRole:
                return entry.media_id
            case WatchedListModel.VideoIdRole:
                return entry.video_id
            case WatchedListModel.TypeRole:
                return entry.type
            case WatchedListModel.NameRole:
                return entry.name
            case WatchedListModel.PosterRole:
                return entry.poster or ""
            case WatchedListModel.LabelRole:
                return entry.label
            case WatchedListModel.ProgressFractionRole:
                return entry.fraction
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            WatchedListModel.MediaIdRole: QByteArray(b"mediaId"),
            WatchedListModel.VideoIdRole: QByteArray(b"videoId"),
            WatchedListModel.TypeRole: QByteArray(b"type"),
            WatchedListModel.NameRole: QByteArray(b"name"),
            WatchedListModel.PosterRole: QByteArray(b"poster"),
            WatchedListModel.LabelRole: QByteArray(b"label"),
            WatchedListModel.ProgressFractionRole: QByteArray(b"progressFraction"),
        }
