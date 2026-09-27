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
        # Reads the latest *unwatched* entry, not latest_for: latest_for would
        # surface a just-finished episode (watched=True, fraction=1.0) as a
        # full bar on the whole show, disagreeing with in_progress() (which
        # excludes watched rows and would say this show isn't in progress).
        entry = progress.latest_unwatched_for(item.id)
        return entry.fraction if entry is not None else 0.0
    return progress.fraction_for(item.id)


def label_for(progress: WatchProgressRepository | None, item: MediaItem) -> str:
    """Names the episode a series would resume into ("S1E3 · Pilot").

    Empty for a movie — its card title already says everything — and empty when
    nothing is in progress. Reads the same entry as `fraction_for`, so the bar
    and the label can never describe different episodes.
    """
    if progress is None or item.type != "series":
        return ""
    entry = progress.latest_unwatched_for(item.id)
    return entry.label if entry is not None else ""


def is_watched(progress: WatchProgressRepository | None, item: MediaItem) -> bool:
    if progress is None:
        return False
    # The (media_id, "") row means "the title itself is finished" for both
    # kinds: for a movie that is the movie; for a series it is the marker
    # written when the user says the show is done. A grid cannot infer that for
    # a series — it has no episode count — but it does not need to: the user
    # said so. One finished EPISODE still never badges the show, because an
    # episode's row is keyed by its own video_id, not "".
    if not progress.is_watched(item.id):
        return False
    # ...unless something is mid-episode again. Resuming a show you had marked
    # finished must yield the badge to the bar, or the poster claims done while
    # showing progress.
    return item.type != "series" or progress.latest_unwatched_for(item.id) is None
