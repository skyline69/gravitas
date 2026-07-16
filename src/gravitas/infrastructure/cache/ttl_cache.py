"""A small bounded TTL cache for responses that are expensive to refetch."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable

# Anything worth caching here is a network round-trip, so entries are large
# enough to matter and few enough to bound cheaply.
DEFAULT_MAX_ENTRIES = 256


class TtlCache[T]:
    """Least-recently-used, with a per-entry lifetime.

    Bounded on purpose: an unbounded response cache is a memory leak with a
    friendly name, and this process is expected to run for hours.

    The clock is monotonic, not wall-clock: a system time jump (NTP, DST, a
    user correcting their clock) must not make a fresh entry look years stale
    or a stale one look fresh.

    Values are handed out by reference, not copied — callers must treat them as
    read-only. Copying every hit would spend most of what the cache saves.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._clock = clock
        self._max_entries = max_entries
        # key -> (expires_at, value), ordered least- to most-recently used.
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            # Drop it now rather than leaving it to occupy a slot until it is
            # evicted by pressure that may never come.
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: T, ttl: float) -> None:
        """Store `value` for `ttl` seconds. A ttl of 0 (or less) stores
        nothing, which is the safe default for anything that must always be
        fetched fresh."""
        if ttl <= 0:
            return
        self._entries[key] = (self._clock() + ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
