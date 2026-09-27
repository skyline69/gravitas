"""Use case: fetch streams and keep only directly-playable URLs (MVP: no torrents)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable
from functools import partial

from gravitas.application.addon_repository import AddonRepository, StreamFetch
from gravitas.domain.errors import NoStreams, SourcesUnavailable
from gravitas.domain.models import MediaType, Stream

_log = logging.getLogger(__name__)

_Key = tuple[MediaType, str]


class ResolveStream:
    """Fetch a video's sources, starting early when asked to.

    A stream addon is slow in a way nothing here can fix: an aggregator such
    as AIOStreams scrapes its providers and checks every result against the
    debrid service before it answers, and five seconds is typical. What the
    client controls is WHEN that wait starts. `prefetch()` starts it before the
    click that needs it -- when a series opens on the episode it will resume,
    when the pointer rests on an episode row -- and a click that lands while
    that request is still running joins it instead of starting a second one.

    A finished list is kept for REUSE_S, so leaving the Sources page and coming
    back (or backing out of a source that would not play) answers at once. That
    is deliberately short, and the reason the addon client itself never caches
    streams still holds: debrid links are time-limited, and one kept for an
    evening fails playback with no obvious cause. A few minutes is well inside
    any link's life. A list that is empty, or that an addon failed to
    contribute to, is not kept at all: the next click should try again, not be
    told the same thing for five minutes.

    Before any of that, the page need not be empty for a video seen before:
    `stored()` hands back the last answer kept on disk, to show during those
    seconds. The fresh answer always replaces it, and until it does the page
    says it is still loading.
    """

    REUSE_S = 5 * 60.0
    # Enough for a season's worth of hovering; finished entries beyond it are
    # dropped oldest first. Running fetches are never dropped -- the table is
    # what keeps them alive (the loop holds tasks only weakly).
    MAX_ENTRIES = 24
    # How many guesses may be in flight at once (see speculate()). Every one is
    # an aggregator request that scrapes its providers and checks the debrid
    # service, so a pointer drifting across a row of posters must not become
    # a burst of them.
    MAX_SPECULATIVE = 2

    def __init__(self, repo: AddonRepository, clock: Callable[[], float] = time.monotonic) -> None:
        self._repo = repo
        self._clock = clock
        self._fetches: OrderedDict[_Key, tuple[float, asyncio.Task[StreamFetch]]] = OrderedDict()
        self._speculative: set[asyncio.Task[StreamFetch]] = set()

    def prefetch(self, type: MediaType, id: str) -> None:
        """Start fetching `id`'s streams now, for a request expected shortly.
        Needs a running loop; a no-op when that video is already covered."""
        self._fetch(type, id)

    def speculate(self, type: MediaType, id: str) -> None:
        """prefetch() on weaker evidence -- a pointer resting on a poster, not
        on the row that opens the list. Dropped outright while MAX_SPECULATIVE
        guesses are already running: a guess is worth a request only while it
        is cheap, and one that is dropped costs nothing but the head start."""
        entry = self._fetches.get((type, id))
        if entry is not None and not entry[1].done():
            return
        self._speculative = {task for task in self._speculative if not task.done()}
        if len(self._speculative) >= self.MAX_SPECULATIVE:
            return
        task = self._fetch(type, id)
        if not task.done():
            self._speculative.add(task)

    async def stored(self, type: MediaType, id: str) -> list[Stream]:
        """The playable part of the last answer kept on disk for this video,
        or [] if there is none -- shown while __call__ fetches the real one.

        Its links may have expired, so it only ever stands in for a fetch that
        is already running. A fetch this session already finished is fresher
        than anything on disk, and __call__ will hand that back at once, so
        there is nothing to stand in for and this answers []."""
        entry = self._fetches.get((type, id))
        if entry is not None and entry[1].done():
            return []
        stored = await self._repo.stored_streams(type, id)
        return [s for s in stored or () if s.is_direct]

    async def __call__(self, type: MediaType, id: str) -> list[Stream]:
        # Shielded: a caller that gives up (the user picked another episode)
        # must not cancel a fetch another caller may be sharing.
        fetch = await asyncio.shield(self._fetch(type, id))
        streams = fetch.streams
        direct = [s for s in streams if s.is_direct]
        if not direct and not fetch.complete:
            # An addon failed, so this empty list is a transport problem and not
            # the addons' answer. Said separately so the page can offer a retry
            # rather than claim there is nothing to watch.
            _log.warning("no playable streams for %s %s: a stream addon did not answer", type, id)
            raise SourcesUnavailable(f"a stream addon did not answer for {id}")
        if not direct:
            # Distinguish "addons had nothing" from "addons had only torrents"
            # — the toast says the same thing either way, the log should not.
            _log.warning(
                "no playable streams for %s %s (%d torrent/external-only dropped)",
                type,
                id,
                len(streams),
            )
            raise NoStreams(f"no direct-URL streams for {id}")
        _log.info(
            "resolved %d playable streams for %s %s (%d dropped as not direct)",
            len(direct),
            type,
            id,
            len(streams) - len(direct),
        )
        return direct

    def _fetch(self, type: MediaType, id: str) -> asyncio.Task[StreamFetch]:
        key: _Key = (type, id)
        now = self._clock()
        entry = self._fetches.get(key)
        if entry is not None:
            finished_at, task = entry
            if not task.done() or now - finished_at <= self.REUSE_S:
                self._fetches.move_to_end(key)
                return task
        task = asyncio.ensure_future(self._repo.fetch_streams(type, id))
        # The timestamp is replaced on completion: a link's age runs from when
        # the addon minted it, not from when someone first asked.
        self._fetches[key] = (now, task)
        task.add_done_callback(partial(self._settle, key))
        self._trim()
        return task

    def _settle(self, key: _Key, task: asyncio.Task[StreamFetch]) -> None:
        entry = self._fetches.get(key)
        if entry is None or entry[1] is not task:
            return  # superseded by a newer fetch of the same video
        keep = (
            not task.cancelled()
            and task.exception() is None
            and task.result().complete
            and bool(task.result().streams)
        )
        if keep:
            self._fetches[key] = (self._clock(), task)
        else:
            del self._fetches[key]

    def _trim(self) -> None:
        excess = len(self._fetches) - self.MAX_ENTRIES
        if excess <= 0:
            return
        for key in [k for k, (_, task) in self._fetches.items() if task.done()][:excess]:
            del self._fetches[key]
