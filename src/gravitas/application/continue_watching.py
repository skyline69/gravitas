"""Use case: the titles to offer for resuming, newest first."""

from __future__ import annotations

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import PlaybackProgress


class ContinueWatching:
    """The Continue Watching row's source.

    No network: `in_progress()` is a dict read, and every field a card needs
    (name, poster, episode label) is denormalized onto the progress entry.
    """

    # Enough to scroll, few enough that the row stays a row. Entries leave by
    # being finished or forgotten, so this caps display only — nothing is
    # pruned from the store.
    DEFAULT_LIMIT = 20

    def __init__(self, progress: WatchProgressRepository) -> None:
        self._progress = progress

    def __call__(self, limit: int = DEFAULT_LIMIT) -> list[PlaybackProgress]:
        return self._progress.in_progress()[:limit]
