"""QObject bridge: forget/reset watch progress and feed the Settings list."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaType
from gravitas.presentation.models.watched_list_model import WatchedListModel


class ProgressController(QObject):
    progressChanged = Signal()
    # Forget events, for mirrors of this state elsewhere (Trakt's paused
    # -playback list): one video / a whole media / everything. Emitted after
    # the local mutation; with nothing connected they are inert.
    progressForgotten = Signal(str, str)  # media_id, video_id ("" = movie row)
    mediaForgotten = Signal(str)
    allProgressReset = Signal()

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

    @Slot(result=int)
    def totalCount(self) -> int:
        """Every saved row, watched included — what Reset all actually
        deletes. Settings gates the Reset all button (and the confirm
        dialog's count) on this, not on inProgressCount(): a user who
        finished every title they started has 0 in-progress rows but dozens
        of watched ones, and the button must still be reachable."""
        return self._progress.total_count()

    # --- mutations ---

    @Slot(str, str)
    def forget(self, media_id: str, video_id: str) -> None:
        self._progress.forget(media_id, video_id)
        self._changed()
        self.progressForgotten.emit(media_id, video_id)

    @Slot(str)
    def forgetMedia(self, media_id: str) -> None:
        self._progress.forget(media_id)
        self._changed()
        self.mediaForgotten.emit(media_id)

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
        self.allProgressReset.emit()

    @Slot()
    def refreshWatched(self) -> None:
        """Repopulate the Settings list (called when the page opens)."""
        self._model.set_entries(self._progress.in_progress())

    @Slot()
    def notifyRecorded(self) -> None:
        """Wired to PlayerController.progressRecorded — the player writes
        progress through a path that never touches this controller's own
        mutations, so without this the `revision` this controller owns (and
        anything bound to it, like Detail's Forget-progress visibility) goes
        stale even though the bars themselves (model roles) refresh fine."""
        self._changed()

    def _changed(self) -> None:
        self._revision += 1
        self._model.set_entries(self._progress.in_progress())
        self.progressChanged.emit()
