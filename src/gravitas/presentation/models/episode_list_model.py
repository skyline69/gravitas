"""Qt list model exposing one season's episodes to the QML Detail page.

Holds only the selected season (not the whole series), so the episode list
stays a couple dozen delegates at most and season switches are cheap resets.
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

from gravitas.domain.models import Video

_ROOT_INDEX = QModelIndex()


class EpisodeListModel(QAbstractListModel):
    VideoIdRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    SeasonRole = Qt.ItemDataRole.UserRole + 3
    EpisodeRole = Qt.ItemDataRole.UserRole + 4
    ThumbnailRole = Qt.ItemDataRole.UserRole + 5
    OverviewRole = Qt.ItemDataRole.UserRole + 6
    ReleasedRole = Qt.ItemDataRole.UserRole + 7

    def __init__(self) -> None:
        super().__init__()
        self._videos: list[Video] = []

    def set_videos(self, videos: list[Video]) -> None:
        self.beginResetModel()
        self._videos = list(videos)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._videos)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        video = self._videos[index.row()]
        match role:
            case EpisodeListModel.VideoIdRole:
                return video.id
            case EpisodeListModel.TitleRole:
                return video.title
            case EpisodeListModel.SeasonRole:
                return video.season if video.season is not None else 0
            case EpisodeListModel.EpisodeRole:
                return video.episode if video.episode is not None else 0
            case EpisodeListModel.ThumbnailRole:
                return video.thumbnail or ""
            case EpisodeListModel.OverviewRole:
                return video.overview or ""
            case EpisodeListModel.ReleasedRole:
                return video.released or ""
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            EpisodeListModel.VideoIdRole: QByteArray(b"videoId"),
            EpisodeListModel.TitleRole: QByteArray(b"title"),
            EpisodeListModel.SeasonRole: QByteArray(b"season"),
            EpisodeListModel.EpisodeRole: QByteArray(b"episode"),
            EpisodeListModel.ThumbnailRole: QByteArray(b"thumbnail"),
            EpisodeListModel.OverviewRole: QByteArray(b"overview"),
            EpisodeListModel.ReleasedRole: QByteArray(b"released"),
        }
