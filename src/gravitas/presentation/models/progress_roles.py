"""The progress lookup shared by every model that shows poster cards.

PosterGridModel and SearchResultsModel both hold MediaItems and need the same
movie-vs-series rule; keeping it here means the rule has one definition.
"""

from __future__ import annotations

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaItem


def fraction_for(progress: WatchProgressRepository | None, item: MediaItem) -> float:
    if progress is None:
        return 0.0
    if item.type == "series":
        # A series poster shows how far into the in-progress episode we are.
        entry = progress.latest_for(item.id)
        return entry.fraction if entry is not None else 0.0
    return progress.fraction_for(item.id)


def is_watched(progress: WatchProgressRepository | None, item: MediaItem) -> bool:
    # One finished episode does not finish a series, and a grid has no episode
    # count to judge by — so only movies ever badge as watched.
    if progress is None or item.type == "series":
        return False
    return progress.is_watched(item.id)
