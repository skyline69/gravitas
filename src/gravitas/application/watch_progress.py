"""In-memory index over a ProgressStore, and every watch-progress policy rule.

Lives in `application` (not `infrastructure`) for the same reason
AddonRepository does: `application` must not import `infrastructure`, so the
store arrives through the ProgressStore port.

The whole table is held in two dicts. Progress is read per grid cell on every
flick, so a read must never touch disk; the table is small enough (one row per
started title) that holding all of it is cheaper than any cache policy.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from gravitas.domain.models import MediaType, PlaybackProgress
from gravitas.domain.ports import ProgressStore

# Under this many seconds a play is a mis-click, not a watch worth remembering.
MIN_POSITION = 30.0
# At or past this fraction of the runtime, the title counts as finished.
WATCHED_AT = 0.9


class WatchProgressRepository:
    def __init__(
        self,
        store: ProgressStore,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._store = store
        self._clock = clock
        self._by_key: dict[tuple[str, str], PlaybackProgress] = {}
        # media_id -> most recently touched entry. What a series poster's bar
        # reads, so it must stay O(1).
        self._latest: dict[str, PlaybackProgress] = {}
        for entry in store.load_all():
            self._index(entry)

    def _index(self, entry: PlaybackProgress) -> None:
        self._by_key[(entry.media_id, entry.video_id)] = entry
        current = self._latest.get(entry.media_id)
        if current is None or entry.updated_at >= current.updated_at:
            self._latest[entry.media_id] = entry

    def _rebuild_latest(self, media_id: str) -> None:
        remaining = [e for key, e in self._by_key.items() if key[0] == media_id]
        if remaining:
            self._latest[media_id] = max(remaining, key=lambda e: e.updated_at)
        else:
            self._latest.pop(media_id, None)

    # --- reads (dict hits; no I/O) ---

    def get(self, media_id: str, video_id: str = "") -> PlaybackProgress | None:
        return self._by_key.get((media_id, video_id))

    def latest_for(self, media_id: str) -> PlaybackProgress | None:
        return self._latest.get(media_id)

    def fraction_for(self, media_id: str, video_id: str = "") -> float:
        entry = self._by_key.get((media_id, video_id))
        return entry.fraction if entry is not None else 0.0

    def is_watched(self, media_id: str, video_id: str = "") -> bool:
        entry = self._by_key.get((media_id, video_id))
        return entry is not None and entry.watched

    def in_progress(self) -> list[PlaybackProgress]:
        """One row per media (the latest touched), unwatched, newest first."""
        rows = [e for e in self._latest.values() if not e.watched]
        rows.sort(key=lambda e: e.updated_at, reverse=True)
        return rows

    def resume_position(self, media_id: str, video_id: str = "") -> float:
        entry = self._by_key.get((media_id, video_id))
        if entry is None or entry.watched:
            return 0.0
        return entry.position

    # --- writes ---

    def record(
        self,
        *,
        media_id: str,
        video_id: str,
        type: MediaType,
        name: str,
        poster: str | None,
        label: str,
        position: float,
        duration: float,
    ) -> None:
        if not media_id or position < MIN_POSITION:
            return
        finished = duration > 0 and position / duration >= WATCHED_AT
        self._put(
            PlaybackProgress(
                media_id=media_id,
                video_id=video_id,
                type=type,
                name=name,
                poster=poster,
                label=label,
                # A finished title drops its position so a replay starts clean.
                position=0.0 if finished else position,
                duration=duration,
                watched=finished,
                updated_at=self._clock(),
            )
        )

    def mark_watched(
        self,
        *,
        media_id: str,
        video_id: str,
        type: MediaType,
        name: str,
        poster: str | None,
        label: str,
    ) -> None:
        if not media_id:
            return
        existing = self._by_key.get((media_id, video_id))
        self._put(
            PlaybackProgress(
                media_id=media_id,
                video_id=video_id,
                type=type,
                name=name,
                poster=poster,
                label=label,
                position=0.0,
                duration=existing.duration if existing is not None else 0.0,
                watched=True,
                updated_at=self._clock(),
            )
        )

    def _put(self, entry: PlaybackProgress) -> None:
        self._index(entry)
        self._store.save(entry)

    def forget(self, media_id: str, video_id: str | None = None) -> None:
        if video_id is None:
            for key in [k for k in self._by_key if k[0] == media_id]:
                del self._by_key[key]
            self._latest.pop(media_id, None)
        else:
            self._by_key.pop((media_id, video_id), None)
            # _latest may have pointed at the row just removed.
            self._rebuild_latest(media_id)
        self._store.delete(media_id, video_id)

    def reset_all(self) -> None:
        self._by_key.clear()
        self._latest.clear()
        self._store.clear()
