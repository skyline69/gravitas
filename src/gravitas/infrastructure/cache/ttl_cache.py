"""A small bounded TTL cache for responses that are expensive to refetch."""

from __future__ import annotations

import sys
import time
from collections import OrderedDict
from collections.abc import Callable

# Anything worth caching here is a network round-trip, so entries are large
# enough to matter and few enough to bound cheaply.
DEFAULT_MAX_ENTRIES = 256

# Entry count alone is a poor bound: addon responses run from a 2 kB manifest
# to a series meta carrying every episode, and a parsed JSON tree costs about
# 3.5x its wire bytes in Python objects (a 115 kB Cinemeta catalog page
# measures 402 kB in memory). 256 entries of that size is ~100 MB of heap for
# a cache nobody sized in bytes. 32 MB holds roughly 80 catalog pages, and the
# disk cache behind this one turns an eviction into a file read.
DEFAULT_MAX_BYTES = 32 * 1024 * 1024


def json_size(value: object) -> int:
    """Approximate the heap a decoded JSON tree occupies, in bytes.

    sys.getsizeof on a dict reports the hash table and nothing it points at,
    so it under-counts a response by orders of magnitude. This walks the tree
    instead, counting each distinct object once — decoded JSON shares string
    objects heavily (every item repeats the same keys), and charging for each
    reference would over-count by as much as getsizeof under-counts.
    """
    seen: set[int] = set()

    def walk(obj: object) -> int:
        marker = id(obj)
        if marker in seen:
            return 0
        seen.add(marker)
        total = sys.getsizeof(obj)
        if isinstance(obj, dict):
            for key, item in obj.items():
                total += walk(key) + walk(item)
        elif isinstance(obj, list | tuple | set):
            for item in obj:
                total += walk(item)
        return total

    return walk(value)


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
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        # key -> (expires_at, value, cost), ordered least- to most-recently
        # used. The cost is measured once, at insertion: values are handed out
        # read-only, so it cannot drift.
        self._entries: OrderedDict[str, tuple[float, T, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value, _ = entry
        if self._clock() >= expires_at:
            # Drop it now rather than leaving it to occupy a slot until it is
            # evicted by pressure that may never come.
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: T, ttl: float, cost: int | None = None) -> None:
        """Store `value` for `ttl` seconds. A ttl of 0 (or less) stores
        nothing, which is the safe default for anything that must always be
        fetched fresh.

        `cost` is the value's size in bytes; it is measured here when omitted.
        Measuring walks the whole tree — 4 ms for a catalog page — so a caller
        that already has the value on a worker thread should measure it there
        and pass the result rather than pay it on the event loop.
        """
        if ttl <= 0:
            return
        cost = json_size(value) if cost is None else cost
        if cost > self._max_bytes:
            # It would evict the entire cache to store one value that still
            # doesn't fit. Refetching it is cheaper than that.
            self._drop(key)
            return
        self._drop(key)
        self._entries[key] = (self._clock() + ttl, value, cost)
        self._bytes += cost
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            evicted, (_, _, evicted_cost) = self._entries.popitem(last=False)
            self._bytes -= evicted_cost
            del evicted

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry[2]

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    @property
    def nbytes(self) -> int:
        """Measured size of everything currently held."""
        return self._bytes

    def __len__(self) -> int:
        return len(self._entries)
