"""In-memory index over a ProgressStore, and every watch-progress policy rule.

Lives in `application` (not `infrastructure`) for the same reason
AddonRepository does: `application` must not import `infrastructure`, so the
store arrives through the ProgressStore port.

The whole table is held in three dicts. Progress is read per grid cell on
every flick, so a read must always be a dict hit -- never a scan, never disk.
Rows are never pruned, so `_by_key` grows monotonically; each dict is
maintained incrementally on write so every read stays O(1) regardless of
table size.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

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
        # media_id -> most recently touched entry, watched or not. Used by
        # hasProgress() and nothing that needs watched-state to be accurate.
        self._latest: dict[str, PlaybackProgress] = {}
        # media_id -> most recently touched *unwatched* entry. What a series
        # poster/row bar reads, so it must stay O(1) like `_latest`.
        self._latest_unwatched: dict[str, PlaybackProgress] = {}
        for entry in store.load_all():
            self._index(entry)

    def _index(self, entry: PlaybackProgress) -> None:
        self._by_key[(entry.media_id, entry.video_id)] = entry
        current = self._latest.get(entry.media_id)
        if current is None or entry.updated_at >= current.updated_at:
            self._latest[entry.media_id] = entry
        self._index_latest_unwatched(entry)

    def _index_latest_unwatched(self, entry: PlaybackProgress) -> None:
        if entry.watched:
            # If this entry just flipped to watched (or was re-saved watched)
            # and it was the pointer, that pointer is now stale. Rebuild from
            # scratch rather than just dropping it: another episode of the
            # same media may still be unwatched and become the new pointer.
            current = self._latest_unwatched.get(entry.media_id)
            if current is not None and current.video_id == entry.video_id:
                self._rebuild_latest_unwatched(entry.media_id)
            return
        current = self._latest_unwatched.get(entry.media_id)
        if current is None or entry.updated_at >= current.updated_at:
            self._latest_unwatched[entry.media_id] = entry

    def _rebuild_latest(self, media_id: str) -> None:
        remaining = [e for key, e in self._by_key.items() if key[0] == media_id]
        if remaining:
            self._latest[media_id] = max(remaining, key=lambda e: e.updated_at)
        else:
            self._latest.pop(media_id, None)

    def _rebuild_latest_unwatched(self, media_id: str) -> None:
        remaining = [e for key, e in self._by_key.items() if key[0] == media_id and not e.watched]
        if remaining:
            self._latest_unwatched[media_id] = max(remaining, key=lambda e: e.updated_at)
        else:
            self._latest_unwatched.pop(media_id, None)

    # --- reads (dict hits; no I/O) ---

    def get(self, media_id: str, video_id: str = "") -> PlaybackProgress | None:
        return self._by_key.get((media_id, video_id))

    def latest_for(self, media_id: str) -> PlaybackProgress | None:
        return self._latest.get(media_id)

    def latest_unwatched_for(self, media_id: str) -> PlaybackProgress | None:
        """Like `latest_for`, but skips watched rows. `_latest` holds a single
        newest-touched entry per media regardless of watched state, so
        finishing episode 1 of a series makes `latest_for` return a
        watched=True/fraction=1.0 row and the whole show reads as finished at
        a full bar — while `in_progress()` (which excludes watched rows)
        simultaneously says the show is NOT in progress. This is what a
        series poster/row bar should read instead, so the two surfaces agree:
        a just-finished episode shows no bar until the next one is started.
        `_latest` isn't enough for this (it discards watched-state
        information across ties), so `_latest_unwatched` is maintained
        alongside it, incrementally, on every write. This is a dict hit like
        every other read here — read per grid cell on every flick, so it must
        stay O(1) regardless of how large `_by_key` grows."""
        return self._latest_unwatched.get(media_id)

    def fraction_for(self, media_id: str, video_id: str = "") -> float:
        entry = self._by_key.get((media_id, video_id))
        return entry.fraction if entry is not None else 0.0

    def is_watched(self, media_id: str, video_id: str = "") -> bool:
        entry = self._by_key.get((media_id, video_id))
        return entry is not None and entry.watched

    def in_progress(self) -> list[PlaybackProgress]:
        """One row per media (its newest unwatched entry), newest first.

        Reads `_latest_unwatched`, not `_latest` filtered by watched: `_latest`
        holds the newest-touched entry whatever its watched state, so finishing
        an OLD episode of a show you are midway through would make `_latest`
        that watched episode, drop it here, and vanish a half-watched show from
        the list — while its poster bar (which reads `_latest_unwatched`) still
        showed real progress. Same dict, same rule, both surfaces agree.
        """
        rows = list(self._latest_unwatched.values())
        rows.sort(key=lambda e: e.updated_at, reverse=True)
        return rows

    def total_count(self) -> int:
        """Every saved row, watched included — what `reset_all` actually
        deletes. `in_progress()` undercounts here on purpose (it drives the
        unwatched-only list), so anything gating "is there something to
        reset" or reporting how many rows a reset will remove must read this
        instead."""
        return len(self._by_key)

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
        if type == "series" and not video_id:
            # Finishing a whole show has to mean it. A bare marker would leave
            # half-watched episodes behind, so the show would keep its bar and
            # its Continue Watching slot while its poster claimed a checkmark.
            # Episodes never started have no row and stay that way — we cannot
            # know they exist from a grid.
            for started in [e for key, e in self._by_key.items() if key[0] == media_id]:
                if not started.watched:
                    self._put(
                        replace(started, position=0.0, watched=True, updated_at=self._clock())
                    )
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
            self._latest_unwatched.pop(media_id, None)
        else:
            self._by_key.pop((media_id, video_id), None)
            # _latest / _latest_unwatched may have pointed at the row just
            # removed.
            self._rebuild_latest(media_id)
            self._rebuild_latest_unwatched(media_id)
        self._store.delete(media_id, video_id)

    def reset_all(self) -> None:
        self._by_key.clear()
        self._latest.clear()
        self._latest_unwatched.clear()
        self._store.clear()
