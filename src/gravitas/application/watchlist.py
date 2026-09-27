"""In-memory index over a WatchlistStore.

Lives in `application` (not `infrastructure`) for the same reason
WatchProgressRepository does: `application` must not import `infrastructure`,
so the store arrives through the WatchlistStore port.

The whole table is one dict. Membership is read per Detail open (and the
entries list per tab switch), so reads must never touch disk; the dict is
maintained incrementally on write. A human curates a watchlist by hand, so it
never grows to a size where startup indexing matters — no pruning.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from gravitas.domain.models import MediaType, WatchlistEntry
from gravitas.domain.ports import WatchlistStore


class WatchlistRepository:
    def __init__(
        self,
        store: WatchlistStore,
        # Wall clock, not monotonic: added_at is persisted and ordered across
        # restarts (same trade-off as WatchProgressRepository's clock).
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._store = store
        self._clock = clock
        self._by_id: dict[str, WatchlistEntry] = {}
        for entry in store.load_all():
            self._by_id[entry.media_id] = entry

    # --- reads (dict hits; no I/O) ---

    def contains(self, media_id: str) -> bool:
        return media_id in self._by_id

    def entries(self) -> list[WatchlistEntry]:
        """Every saved title, newest first."""
        return sorted(self._by_id.values(), key=lambda e: e.added_at, reverse=True)

    # --- writes ---

    def add(
        self,
        *,
        media_id: str,
        type: MediaType,
        name: str,
        poster: str | None,
        year: str | None,
    ) -> None:
        if not media_id:
            return
        existing = self._by_id.get(media_id)
        entry = WatchlistEntry(
            media_id=media_id,
            type=type,
            name=name,
            poster=poster,
            year=year,
            # Re-adding refreshes the metadata but keeps the original slot:
            # "update the poster" must not silently bump a title to the top.
            added_at=existing.added_at if existing is not None else self._clock(),
        )
        self._by_id[media_id] = entry
        self._store.save(entry)

    def remove(self, media_id: str) -> None:
        if self._by_id.pop(media_id, None) is not None:
            self._store.delete(media_id)
