"""QObject bridge: forget/reset watch progress and feed the Settings list."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaType
from gravitas.presentation.models.watched_list_model import WatchedListModel


class ProgressController(QObject):
    progressChanged = Signal()

    def __init__(self, progress: WatchProgressRepository, model: WatchedListModel) -> None:
        super().__init__()
        self._progress = progress
        self._model = model
        self._revision = 0

    @Property(int, notify=progressChanged)
    def revision(self) -> int:
        """Bumped on every mutation. A QML binding that calls hasProgress()
        must read `revision` too — a Slot call is not a reactive dependency,
        so without it the binding never re-evaluates when progress changes."""
        return self._revision

    # --- queries ---

    @Slot(str, result=bool)
    def hasProgress(self, media_id: str) -> bool:
        return self._progress.latest_for(media_id) is not None

    @Slot(str, str, result=bool)
    def isWatched(self, media_id: str, video_id: str) -> bool:
        return self._progress.is_watched(media_id, video_id)

    @Slot(result=int)
    def inProgressCount(self) -> int:
        return len(self._progress.in_progress())

    # --- mutations ---

    @Slot(str, str)
    def forget(self, media_id: str, video_id: str) -> None:
        self._progress.forget(media_id, video_id)
        self._changed()

    @Slot(str)
    def forgetMedia(self, media_id: str) -> None:
        self._progress.forget(media_id)
        self._changed()

    @Slot("QVariantMap")
    def markWatched(self, context: dict[str, object]) -> None:
        media_id = str(context.get("mediaId", ""))
        if not media_id:
            return
        media_type: MediaType = "series" if context.get("type") == "series" else "movie"
        poster = context.get("poster")
        self._progress.mark_watched(
            media_id=media_id,
            video_id=str(context.get("videoId", "")),
            type=media_type,
            name=str(context.get("name", "")),
            poster=str(poster) if poster else None,
            label=str(context.get("label", "")),
        )
        self._changed()

    @Slot()
    def resetAll(self) -> None:
        self._progress.reset_all()
        self._changed()

    @Slot()
    def refreshWatched(self) -> None:
        """Repopulate the Settings list (called when the page opens)."""
        self._model.set_entries(self._progress.in_progress())

    def _changed(self) -> None:
        self._revision += 1
        self._model.set_entries(self._progress.in_progress())
        self.progressChanged.emit()
