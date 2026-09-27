"""Resolve the likeliest source's link before it is clicked.

The Sources list's top row is the one most likely to be played; resolving
its redirect while the viewer reads the list takes the addon's round trip
(~1.3s measured, a debrid lookup) out of the wait after the click. One link
per list, not every row: each resolve is a debrid API call on the viewer's
account.

A resolved link is handed out once (`take`), and only while young. Debrid
links expire, so an old one is not worth the risk; and if a young one fails
anyway, the player falls back to the original URL, which must then be asked
fresh rather than answered from here again.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence

from gravitas.domain.ports import LinkResolver


class LinkWarmup:
    # Well inside a debrid link's life (hours), and longer than anyone reads
    # a source list.
    TTL_S = 10 * 60.0
    MAX_ENTRIES = 32

    def __init__(self, resolver: LinkResolver, clock: Callable[[], float] = time.monotonic) -> None:
        self._resolver = resolver
        self._clock = clock
        self._resolved: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._running: dict[str, asyncio.Task[None]] = {}

    def warm(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> None:
        """Start resolving `url`, unless it is already resolved or resolving.
        Needs a running loop."""
        if not url or url in self._running or self._fresh(url) is not None:
            return
        task = asyncio.ensure_future(self._resolve(url, tuple(headers)))
        self._running[url] = task
        task.add_done_callback(lambda _: self._running.pop(url, None))

    def take(self, url: str) -> str | None:
        """The resolved link for `url` if one is young enough -- once."""
        resolved = self._fresh(url)
        self._resolved.pop(url, None)
        return resolved

    def _fresh(self, url: str) -> str | None:
        entry = self._resolved.get(url)
        if entry is None or self._clock() - entry[1] > self.TTL_S:
            return None
        return entry[0]

    async def _resolve(self, url: str, headers: tuple[tuple[str, str], ...]) -> None:
        resolved = await self._resolver.resolve(url, headers)
        if resolved is None:
            return
        self._resolved[url] = (resolved, self._clock())
        self._resolved.move_to_end(url)
        while len(self._resolved) > self.MAX_ENTRIES:
            self._resolved.popitem(last=False)
