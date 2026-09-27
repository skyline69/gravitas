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

from gravitas.application.watch_progress import WatchProgressRepository
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
    ProgressFractionRole = Qt.ItemDataRole.UserRole + 8
    WatchedRole = Qt.ItemDataRole.UserRole + 9

    def __init__(self, progress: WatchProgressRepository | None = None) -> None:
        super().__init__()
        self._videos: list[Video] = []
        self._progress = progress
        self._media_id = ""

    @property
    def media_id(self) -> str:
        return self._media_id

    def set_media_id(self, media_id: str) -> None:
        """The series these episodes belong to — progress is keyed by it."""
        self._media_id = media_id

    def set_videos(self, videos: list[Video]) -> None:
        self.beginResetModel()
        self._videos = list(videos)
        self.endResetModel()

    def refresh_progress(self) -> None:
        """Re-read the progress roles for every row (the underlying dict moved)."""
        if not self._videos:
            return
        self.dataChanged.emit(
            self.index(0, 0, _ROOT_INDEX),
            self.index(len(self._videos) - 1, 0, _ROOT_INDEX),
            [EpisodeListModel.ProgressFractionRole, EpisodeListModel.WatchedRole],
        )

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
            case EpisodeListModel.ProgressFractionRole:
                if self._progress is None:
                    return 0.0
                return self._progress.fraction_for(self._media_id, video.id)
            case EpisodeListModel.WatchedRole:
                if self._progress is None:
                    return False
                return self._progress.is_watched(self._media_id, video.id)
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
            EpisodeListModel.ProgressFractionRole: QByteArray(b"progressFraction"),
            EpisodeListModel.WatchedRole: QByteArray(b"watched"),
        }
