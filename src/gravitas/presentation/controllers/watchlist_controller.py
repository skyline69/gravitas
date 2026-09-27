"""QObject bridge: toggle watchlist membership and feed the Watchlist grid."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from gravitas.application.watchlist import WatchlistRepository
from gravitas.domain.models import MediaItem, MediaType
from gravitas.presentation.models.poster_grid_model import PosterGridModel


class WatchlistController(QObject):
    watchlistChanged = Signal()

    def __init__(
        self,
        watchlist: WatchlistRepository,
        movies_model: PosterGridModel,
        series_model: PosterGridModel,
    ) -> None:
        super().__init__()
        self._watchlist = watchlist
        # One model per section: the Watchlist page shows Movies and Series
        # under their own headers, and QML cannot filter a QAbstractListModel
        # by role without a proxy each.
        self._movies_model = movies_model
        self._series_model = series_model
        self._revision = 0
        self._refresh_models()

    @Property(int, notify=watchlistChanged)
    def revision(self) -> int:
        """Bumped on every mutation. A QML binding that calls contains()
        must read `revision` too — a Slot call is not a reactive dependency,
        so without it the binding never re-evaluates when the list changes."""
        return self._revision

    # --- queries ---

    @Slot(str, result=bool)
    def contains(self, media_id: str) -> bool:
        return self._watchlist.contains(media_id)

    @Slot(result=int)
    def count(self) -> int:
        return len(self._watchlist.entries())

    # --- mutations ---

    @Slot("QVariantMap")
    def toggle(self, context: dict[str, object]) -> None:
        """Add or remove one title. Keys: mediaId, type, name, poster, year."""
        media_id = str(context.get("mediaId", ""))
        if not media_id:
            return
        if self._watchlist.contains(media_id):
            self._watchlist.remove(media_id)
        else:
            media_type: MediaType = "series" if context.get("type") == "series" else "movie"
            poster = context.get("poster")
            year = context.get("year")
            self._watchlist.add(
                media_id=media_id,
                type=media_type,
                name=str(context.get("name", "")),
                poster=str(poster) if poster else None,
                year=str(year) if year else None,
            )
        self._changed()

    @Slot(str)
    def remove(self, media_id: str) -> None:
        self._watchlist.remove(media_id)
        self._changed()

    # --- assembly ---

    def _refresh_models(self) -> None:
        items = [
            MediaItem(
                id=entry.media_id,
                type=entry.type,
                name=entry.name,
                poster=entry.poster,
                year=entry.year,
            )
            for entry in self._watchlist.entries()
        ]
        self._movies_model.set_items([item for item in items if item.type == "movie"])
        self._series_model.set_items([item for item in items if item.type == "series"])

    def _changed(self) -> None:
        self._revision += 1
        self._refresh_models()
        self.watchlistChanged.emit()
