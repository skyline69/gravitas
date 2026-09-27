"""Fetch one stream over several connections at once, and serve it to mpv.

**Why this exists.** mpv reads a stream through ffmpeg's HTTP client, which
opens exactly one connection and reads it front to back. There is no mpv
option for more: not `--stream-lavf-o`, not the cache settings, nothing. So
when a host caps what a *single* connection may do -- which is how most debrid
services shape traffic, and what makes a 25 Mbps file stall on a 300 Mbps line
-- the player has no way to ask for the rest of the pipe. That cap is invisible
from inside mpv; it looks exactly like a slow line.

So the parallelism is put in front of mpv instead. This is a small HTTP server
on 127.0.0.1 that mpv plays from. It answers one range request by issuing
several against the real host at once, each for a different block, and writes
the blocks out in order as they land. mpv sees an ordinary, well-behaved HTTP
server; the host sees N readers; the viewer sees the aggregate.

**What it refuses to do.** It never guesses. A host that does not answer 206
with a `Content-Range`, or does not say how long the file is, gets one plain
connection and a straight copy -- the same thing mpv would have done, no worse.
The same applies to anything that is not http/https, which is how `ytId`
streams (mpv resolves those through `ytdl_hook`, into an EDL of its own) stay
on the path that works for them.

**And it gets out of the way.** When the proxy cannot serve a stream -- the
request fails before a byte is sent -- it answers **302 to the upstream URL**.
ffmpeg follows that by itself, in the same request, so playback continues
directly and nothing above ever learns there was a problem. This matters more
than it looks: closing the socket instead left mpv reconnecting to a local URL
that could never work, and its own retry loop does not give up, so the spinner
never ended. A proxy that fails has to fail *out of the path*, not in it.

**Except when the host refused the connection.** A 302 says "try this URL
instead", and that is only useful advice if the URL might work. When the
failure is a refused or unreachable *connect* -- which is what a dead debrid
CDN node looks like -- the proxy has just proved from this machine that mpv
cannot open it either, and pointing mpv at it buys nothing but ffmpeg's own
retry ladder before the same answer arrives ten seconds later. Every source
the addon signed to that node fails the same way, so those seconds are paid
once per source in the fallback queue. A **502** instead ends the load at
once, which is what lets the player move to the next source immediately.

**Ordering, memory and seeks.** Everything is cut to an aligned chunk grid and
kept in a per-stream cache, which is what makes a seek cheap: mpv answers a
seek by opening a *new connection to the same URL*, so whatever the previous
one pulled is already here and is written straight out. Chunks are claimed
from one cursor, so two workers never fetch the same bytes, and the cursor may
not run more than `READAHEAD_CHUNKS` past what mpv is reading. The cache is
bounded and evicts furthest-from-playhead, because going back and going
forward are equally likely and the bytes under the playhead are the ones worth
keeping.

**The backfill.** Resuming an episode at 3:51 means nothing before 3:51 has
ever been downloaded, so stepping back is a cold fetch however good the cache
is. One extra worker walks *backwards* from the starting position, and it runs
only while the writer is parked in `drain()` -- that is mpv refusing bytes
because its own cache is full, which is exactly and only when there is spare
bandwidth to spend. The moment mpv wants something again, the backfill stops.

**Measuring the line.** With a proxy in the path, mpv's own `cache-speed`
measures localhost, which is not a fact about anybody's internet. The bytes
actually pulled from the host are counted here instead, and
`upstream_bytes_per_s()` is what the player samples while a stream is proxied.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import shutil
import threading
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx

from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)

# How many connections to pull one stream over. Past a handful the gain is
# small and the host starts to look like something worth rate-limiting; this
# is the number that turns a per-connection cap into a non-issue without
# behaving like a download accelerator. Four, not six: a TorBox CDN measured
# answering 2 of 6 concurrent requests on one file with 429 (and 15 of 30 in
# six-way bursts), while one connection alone ran at 192 Mbps -- the speed was
# never the issue, and the start-up gain comes from reused connections and the
# chunk cache, not from how many there are.
CONNECTIONS = 4
# How long what a host taught about its connection limit is remembered. A
# new stream is a new URL (a new debrid token), and starting it back at the
# full count re-earned the same 429s on every playback: the one measured went
# 6 -> 1 over five seconds of stalled start-up. Kept per host, and forgotten
# after this, since the limit can lift and nothing else would ever notice.
_HOST_LIMIT_TTL_S = 15 * 60.0
# One worker's unit of work, once the stream is running. Big enough that a
# block amortises its own request round trip, small enough that six of them in
# flight is tens of megabytes and that a stall on one block delays the write
# queue by a fraction of a second of video, not by seconds.
# The grid everything is cut to. Aligned on purpose: a chunk fetched for one
# position has to be reusable at another, and a range starting anywhere inside
# it has to hit the same entry. 1 MiB is about a third of a second of a 25 Mbps
# stream -- small enough that the very first one is not a wait anybody notices,
# large enough that its request overhead is nothing next to its body.
CHUNK_BYTES = 1024 * 1024
# How far ahead of what mpv is reading the workers may run. This is the
# proxy's own memory window, separate from the cache: past it the workers wait
# rather than racing down a file mpv has not asked for.
READAHEAD_CHUNKS = 24
# How many consecutive chunks one upstream request covers. The cache is cut
# into 1 MiB chunks, and one request per chunk was one request per ~40ms on a
# fast line: the proxy's start-up ran at ~20 requests/second, and a TorBox CDN
# answered that with 429 on half of them (while tolerating the same bytes
# asked for a few requests at a time). A worker now claims a run of chunks and
# streams it in one ranged request, handing each chunk to the cache as soon as
# its last byte arrives -- so the first chunk of a run is no slower than a
# chunk on its own was, and the request count drops by up to this factor.
RUN_CHUNKS = 8
# How much of a response body is read at a time while it is cut into chunks.
_READ_PIECE_BYTES = 64 * 1024
# What one stream may hold. Covers what was fetched ahead, what was played,
# and what the backfill pulled in behind the starting position -- so a seek
# into any of it is served without touching the network.
CACHE_MAX_BYTES = 192 * 1024 * 1024
# The same cache on disk, when the app gives the proxy a directory. Then it
# is the stream's main cache and the player keeps only a small one of its own
# (see holds_stream_cache): 2 GiB ahead of mpv's read position and 1 GiB
# behind it -- the same window mpv's own on-disk cache held, so a machine
# pays for the stream once, not twice. Ahead is what carries playback through
# a network drop; behind is what makes a seek back, and a track switch's
# refresh seek, a local read.
DISK_CACHE_MAX_BYTES = 3 * 1024 * 1024 * 1024
DISK_CACHE_BEHIND_BYTES = 1024 * 1024 * 1024
# How far behind the starting position to fetch on spare bandwidth. Resuming
# an episode at 3:51 means nothing before 3:51 has ever been downloaded, so
# stepping back is a cold fetch unless something goes and gets it. At 25 Mbps
# this is about 30 seconds of video, at 8 Mbps about a minute and a half.
BACKFILL_BYTES = 96 * 1024 * 1024

_BLOCK_ATTEMPTS = 3
_RETRY_DELAY_S = 0.5
# A host saying "too many": 429, and 503 when it comes with a Retry-After.
# This is not a failure of the stream, it is the host asking for fewer
# readers, and the answer is fewer readers -- never a dead playback.
_RATE_LIMIT_STATUSES = frozenset({429, 503})
# Attempts a rate-limited block gets, on top of being slowed down. Generous
# because the alternative is ending mpv's connection mid-episode, and because
# mpv is reading out of a cache measured in minutes while this waits.
_RATE_LIMIT_ATTEMPTS = 10
_RATE_LIMIT_DELAYS_S = (0.25, 0.5, 1.0, 2.0, 4.0)
# Tries the probe gets against a rate limit before this one request gives up:
# about 12s on the steps above, well inside the 60s mpv allows a proxied load.
_PROBE_RATE_LIMIT_ATTEMPTS = 6
# Six workers over the limit all hear "too many" at once, and each answer is
# evidence of the same one limit. Without this they would shrink the stream to
# a single reader on one round trip, which throws away the whole point of the
# module on a host that only wanted one reader fewer.
_SHRINK_DEBOUNCE_S = 0.5
# The longest a `Retry-After` is believed. Past this the host is not asking
# for a pause, it is asking for a different plan, and the plan is one reader.
_MAX_RETRY_AFTER_S = 30.0

# Upstream timeouts. Generous on read (a host under load is not a failure) and
# short on connect (a host that will not answer should free its worker).
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=30.0)
# Rates are reported over this window, so a number sampled during a cache fill
# describes the fill and not the whole episode.
_RATE_WINDOW_S = 10.0
# Below this the elapsed time says more about scheduling than throughput.
_MIN_RATE_SPAN_S = 0.05

# How long a write to mpv may block before the connection is treated as gone.
# macOS does not fail a write to a peer that has closed until it sends an RST,
# so a `drain()` on a dead socket can simply never return -- which is how six
# workers stayed pinned to a connection nobody was reading, and then how the
# next playback starved on the connection pool.
_WRITE_TIMEOUT_S = 30.0
# A block is handed to the socket in slices this size rather than in one go.
# Writing 4 MiB in a single call buffers the lot inside the transport and runs
# the copy without yielding, which is a burst of work once per block; sliced,
# the same bytes move as a steady trickle the loop can interleave with the
# workers that are filling the next block.
_WRITE_SLICE_BYTES = 256 * 1024
# How long the teardown waits for cancelled workers before giving up on them.
# Bounded on purpose: the handler must return so its task leaves the live set,
# and `stopping` below means a straggler finishes its own block and stops
# rather than carrying on down the file.
_CLEANUP_TIMEOUT_S = 5.0
# How long teardown lets the workers stop by themselves before cancelling
# them. Cancelling an httpx request that is mid-`connect_tcp` can leave a slot
# held in httpcore's pool, and closing a client with a held slot blocks -- so
# the graceful path is the normal path, and cancellation is the fallback for a
# worker that is genuinely stuck.
_GRACE_TIMEOUT_S = 1.0
# Handler failures on one stream before the proxy takes itself out of the path
# for it. "No worse than mpv on its own" has to hold when this module is the
# thing that is broken. Note this is only an optimization: every failure
# redirects mpv to the real URL there and then, so playback is already
# recovered. This just stops the next connection spending a round trip
# rediscovering it. Two, not one, because a single dropped connection is not
# proof that a host is unreachable.
_MAX_ENTRY_FAILURES = 2
# How many of those to remember. One playback at a time, so this only needs to
# outlive a reconnect or two.
_BROKEN_LIMIT = 8
# How long start() waits for the proxy thread to be listening.
_START_TIMEOUT_S = 5.0

# Guard rails on what is read off the socket before a request is understood.
_MAX_REQUEST_BYTES = 64 * 1024
_HEADER_TIMEOUT_S = 15.0


@dataclass(slots=True)
class _ResponseState:
    """Whether a response has begun. Once a status line is out, the only
    honest thing left is to finish or close; before it, the request can still
    be handed somewhere else."""

    started: bool = False


class _RateLimited(Exception):
    """The host answered "too many requests". Not a failed block: a block it
    has not served *yet*, and an instruction to use fewer connections."""

    def __init__(self, after: float | None = None) -> None:
        super().__init__(f"rate limited, retry after {after}s" if after else "rate limited")
        self.after = after


def _retry_after_s(response: httpx.Response) -> float | None:
    """`Retry-After` in seconds, when the host gave one and it is sane.

    Only the delta-seconds form is read. An HTTP-date is legal too, but a
    clock difference turns it into either no wait at all or a wait longer than
    the viewer will sit through, and the backoff below is a better answer than
    a date this machine cannot trust.
    """
    raw = response.headers.get("retry-after", "").strip()
    if not raw.isdigit():
        return None
    return min(float(raw), _MAX_RETRY_AFTER_S)


class _ClientStalled(Exception):
    """The player stopped reading for _WRITE_TIMEOUT_S. Almost always a pause
    with the player's own buffer full; the player reconnects when it wants
    more, so this ends a connection, not a playback."""


class _ProbeFailed(Exception):
    """The probe could not reach the host. Distinct from "the host answered
    and does not do ranges", which is a fact worth remembering."""


def _unreachable_host(exc: BaseException) -> str | None:
    """The host of a request that never got a connection, or None.

    Only a connect failure counts. A read that died mid-response, a timeout
    waiting for a body, a bad status -- those say the host is there and
    something else went wrong, and mpv may well do better with it than this
    module did. A refused connect is different: httpx and ffmpeg dial from the
    same machine through the same resolver, so a host that will not take a
    connection here will not take one from mpv either.
    """
    if not isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return None
    request = getattr(exc, "request", None)
    host = getattr(getattr(request, "url", None), "host", None)
    return str(host) if host else "?"


# Addresses a resolver hands back when it is refusing to answer rather than
# failing to: a blocklist sinkhole. The connection attempt that follows is
# refused instantly, which is indistinguishable from a dead host unless the
# address is looked at.
_SINKHOLE_ADDRESSES = frozenset({"0.0.0.0", "::", "::1"})


async def _describe_unreachable(host: str) -> str:
    """One line a viewer can act on, saying why `host` took no connection.

    A refused node and a host the machine's own DNS has sinkholed look
    identical from the socket -- both fail at once -- and the answer is
    completely different: wait for the debrid service, or look at the
    resolver. The addresses say which, so they are read once, on the failure
    path only.
    """
    loop = asyncio.get_running_loop()
    try:
        info = await loop.getaddrinfo(host, None)
    except Exception:
        return f"{host} does not resolve on this network."
    addresses = {str(entry[4][0]) for entry in info}
    if addresses and addresses <= _SINKHOLE_ADDRESSES:
        return f"{host} is blocked by this machine's DNS (it resolves to 0.0.0.0)."
    return f"{host} refused the connection."


@dataclass(slots=True)
class _Entry:
    """One registered upstream stream, and what has been learned about it."""

    url: str
    headers: tuple[tuple[str, str], ...] = ()
    # Where the probe ended up after redirects. Addon URLs here are a signing
    # endpoint that 302s to a CDN, and re-walking that chain for every 4 MiB
    # block is a round trip per block -- on a slow signer, seconds per block.
    resolved: str = ""
    size: int | None = None
    ranged: bool | None = None  # None until the first probe answers
    probe_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Shared by every connection for this stream, which is what makes a seek
    # cheap: mpv answers a seek by opening a NEW connection to the same URL,
    # and anything already here is served without asking the host again.
    cache: _ChunkCache = field(default_factory=lambda: _ChunkCache(CACHE_MAX_BYTES))
    # Chunks a worker is fetching right now. Keeps two connections (or the
    # backfill and the playhead) from fetching the same bytes twice.
    inflight: set[int] = field(default_factory=set)
    # A chunk landed. Every waiter re-checks the cache on it.
    landed: asyncio.Event = field(default_factory=asyncio.Event)
    # The lowest chunk the backfill has reached, and the floor it stops at.
    backfill_next: int = -1
    backfill_floor: int = 0
    # Handler failures for this stream. Past _MAX_ENTRY_FAILURES the proxy
    # stops offering to serve it at all -- see `_broken`.
    failures: int = 0
    # How many forward workers this stream may run right now. Starts at the
    # configured count and only ever shrinks, on the host's own say-so (a 429
    # answer). Shared by every connection for this stream, so a seek does not
    # go back to hammering a host that has already objected once.
    parallel: int = CONNECTIONS
    # When it last shrank, so a burst of 429s from workers that were already
    # in flight counts as the one limit it is.
    shrunk_at: float = 0.0


def is_proxyable(url: str) -> bool:
    """Whether this URL is one the proxy can stand in front of.

    http/https only. A `ytdl://` or EDL URL is resolved by mpv itself into
    something else entirely, and a local file has nothing to parallelise.
    """
    return urlsplit(url).scheme in ("http", "https")


def parse_range(value: str, size: int | None) -> tuple[int, int] | None:
    """`(first, last)` inclusive for a `Range: bytes=...` header, or None when
    it is absent, malformed, or a form this server does not serve.

    Only the two forms mpv sends are honoured -- `bytes=N-` and `bytes=N-M`.
    A suffix range (`bytes=-N`) needs the size, and is answered only when the
    size is known.
    """
    if not value.lower().startswith("bytes="):
        return None
    spec = value[len("bytes=") :].strip()
    if "," in spec:  # multipart ranges: not served, and mpv never asks
        return None
    first_text, _, last_text = spec.partition("-")
    try:
        if not first_text:
            if size is None or not last_text:
                return None
            length = int(last_text)
            if length <= 0:
                return None
            return (max(0, size - length), size - 1)
        first = int(first_text)
        last = int(last_text) if last_text else (size - 1 if size is not None else -1)
    except ValueError:
        return None
    if first < 0 or (last >= 0 and last < first):
        return None
    if size is not None:
        if first >= size:
            return None
        last = min(last, size - 1) if last >= 0 else size - 1
    return (first, last)


class _ChunkCache:
    """Aligned chunks of one stream, bounded, evicted by distance from the
    chunk mpv is reading (`anchor`).

    Two jobs, and they are the same mechanism. It keeps a seek backwards from
    re-downloading what was already played, and it holds what the backfill
    pulled in behind the starting position.

    In memory by default; on disk (one file per chunk under `directory`) when
    the proxy is the stream's main cache. That is what makes a track switch
    cheap: mpv answers a new subtitle or audio track with a "refresh seek" --
    back to the playing position, re-reading forward to collect the new
    track's packets -- and those bytes are only local if something kept
    everything between a little behind playback and mpv's read position. With
    mpv reading gigabytes ahead, a 192 MiB in-memory cache had evicted them
    minutes earlier, and every switch was a network fetch behind a spinner.

    Eviction is asymmetric: up to `limit - behind` bytes ahead of the anchor
    are what the proxy is reading ahead into, `behind` bytes behind it are
    what was played. Whichever side is furthest over its own share goes first.
    """

    def __init__(
        self, limit: int, *, behind: int | None = None, directory: Path | None = None
    ) -> None:
        self._sizes: dict[int, int] = {}
        # Memory mode only; on disk the bytes live in files.
        self._chunks: dict[int, bytes] = {}
        self._limit = limit
        self._behind = max(1, behind if behind is not None else limit // 2)
        self._ahead = max(1, limit - self._behind)
        self._total = 0
        self._dir = directory
        self._closed = False
        self.anchor = 0
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    def __contains__(self, index: int) -> bool:
        return index in self._sizes

    def __len__(self) -> int:
        return len(self._sizes)

    @property
    def total_bytes(self) -> int:
        return self._total

    def _path(self, index: int) -> Path:
        assert self._dir is not None
        return self._dir / f"{index}.chunk"

    def get(self, index: int) -> bytes | None:
        if index not in self._sizes:
            return None
        if self._dir is None:
            return self._chunks.get(index)
        try:
            return self._path(index).read_bytes()
        except OSError:
            # Gone underneath us (a cleaned-up directory, a full disk that lost
            # the write): not cached, so the writer asks for it again.
            self._forget(index)
            return None

    def put(self, index: int, data: bytes) -> None:
        if index in self._sizes or self._closed:
            return
        if self._dir is None:
            self._chunks[index] = data
        else:
            try:
                self._path(index).write_bytes(data)
            except OSError:
                return  # a full disk costs the cache, never the stream
        self._sizes[index] = len(data)
        self._total += len(data)
        self._evict()

    def close(self) -> None:
        """Drop everything, and the directory with it. Later puts are ignored:
        a handler still tearing down may land one more chunk."""
        self._closed = True
        self._chunks.clear()
        self._sizes.clear()
        self._total = 0
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)

    def _overshoot(self, index: int) -> float:
        # How far past its side's share a chunk sits, as a fraction of that
        # share -- so a gigabyte ahead and half a gigabyte behind compare
        # fairly when the shares are two to one.
        if index >= self.anchor:
            return (index - self.anchor) / self._ahead
        return (self.anchor - index) / self._behind

    def _forget(self, index: int) -> None:
        size = self._sizes.pop(index, 0)
        self._total -= size
        self._chunks.pop(index, None)
        if self._dir is not None:
            with contextlib.suppress(OSError):
                self._path(index).unlink()

    def _evict(self) -> None:
        while self._total > self._limit and len(self._sizes) > 1:
            victim = max(self._sizes, key=self._overshoot)
            # Never the chunk being read: at a limit smaller than one chunk
            # that is the only thing standing between this and a refetch loop.
            if victim == self.anchor:
                return
            self._forget(victim)


class _RateMeter:
    """Bytes per second off the upstream host, over a short trailing window."""

    def __init__(self) -> None:
        self._samples: list[tuple[float, int]] = []
        # Written on the proxy thread, read on the GUI thread by the player's
        # speed sampler. Cheap, and uncontended in practice: one append per
        # block against one read every five seconds.
        self._lock = threading.Lock()

    def add(self, count: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, count))
            self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - _RATE_WINDOW_S
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def bytes_per_s(self) -> float:
        """0.0 until there is enough of a span to divide by. A single block
        that landed a millisecond ago is not a rate -- reporting one would
        hand the connection estimate a number in the gigabits."""
        now = time.monotonic()
        with self._lock:
            self._trim(now)
            if not self._samples:
                return 0.0
            span = now - self._samples[0][0]
            if span < _MIN_RATE_SPAN_S:
                return 0.0
            return sum(count for _, count in self._samples) / span


class SegmentedStreamProxy:
    """A StreamAccelerator: registers upstream URLs, serves them on localhost.

    **Everything here runs on its own thread, with its own event loop, and
    that is not an implementation detail.** The app's asyncio loop *is* Qt's
    event loop (qasync), so a coroutine scheduled on it runs on the GUI
    thread -- the thread that also drives QML animations and the scene graph
    sync. Serving a 25 Mbps stream means TLS decryption, HTTP body assembly
    and a multi-megabyte socket write arriving in bursts, roughly one per
    second at these bitrates, and on the GUI thread that lands as a visible
    hitch in the video once a second. ffmpeg did none of this on the GUI
    thread, because ffmpeg is not Python. So the proxy does not either.

    Registration is deliberately synchronous and does no I/O -- it hands back
    a URL and nothing else. That is what lets `PlayerController.play()` stay
    the plain, synchronous call it is, and it is also why registration is the
    one thing that crosses threads: the dict it writes is guarded by a lock,
    and nothing else on this object is touched from outside.
    """

    def __init__(
        self,
        *,
        connections: int = CONNECTIONS,
        chunk_bytes: int = CHUNK_BYTES,
        cache_bytes: int | None = None,
        backfill_bytes: int = BACKFILL_BYTES,
        cache_dir: Path | None = None,
    ) -> None:
        self._connections = max(1, connections)
        self._chunk_bytes = max(16 * 1024, chunk_bytes)
        # On disk when given a directory, in memory otherwise (tests, and a
        # machine whose cache directory cannot be written).
        self._cache_dir = cache_dir
        if cache_bytes is None:
            cache_bytes = DISK_CACHE_MAX_BYTES if cache_dir is not None else CACHE_MAX_BYTES
        self._cache_bytes = max(self._chunk_bytes, cache_bytes)
        self._cache_behind = (
            min(DISK_CACHE_BEHIND_BYTES, self._cache_bytes // 3)
            if cache_dir is not None
            else self._cache_bytes // 2
        )
        self._backfill_bytes = max(0, backfill_bytes)
        self._entries: dict[str, _Entry] = {}
        # Upstream URLs this proxy has failed to serve. Registering one of
        # them again hands back the URL itself, so playback continues without
        # the proxy rather than failing with it.
        self._broken: list[str] = []
        # Why the last stream could not be reached, ready to show. Written on
        # the proxy thread, read on the GUI thread; under _entries_lock, like
        # everything else that crosses.
        self._last_failure: str | None = None
        # Guards _entries only, which the GUI thread writes (local_url) and
        # the proxy thread reads (a request arriving).
        self._entries_lock = threading.Lock()
        self._server: asyncio.AbstractServer | None = None
        # Every in-flight request handler, so shutdown can end them. A handler
        # that outlives its client holds workers, and workers hold upstream
        # connections out of the pool.
        self._handlers: set[asyncio.Task[None]] = set()
        self._port = 0
        self._meter = _RateMeter()
        # The component default; the app sets this from the stored setting
        # (see PersistedSettings).
        self.enabled: bool = True
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        # host -> (connections it was last seen to accept, when). Read and
        # written under _entries_lock: local_url runs on the GUI thread, the
        # shrink on the proxy's own.
        self._host_limits: dict[str, tuple[int, float]] = {}

    # --- lifecycle ---

    async def start(self) -> None:
        """Bring the proxy thread up and wait for it to be listening.

        Failing to bind is not fatal: `local_url` keeps handing back the
        upstream URL, and playback is what it was before this existed.
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="gravitas-stream-proxy", daemon=True)
        self._thread.start()
        # Off the calling loop: the GUI thread must not block on a socket
        # bind, however brief.
        await asyncio.to_thread(self._ready.wait, _START_TIMEOUT_S)
        if self._error is not None:
            _log.warning("stream accelerator did not start: %s", self._error)
            return
        _log.info(
            "segmented stream proxy on 127.0.0.1:%d (%d connections per stream, own thread)",
            self._port,
            self._connections,
        )

    def _run(self) -> None:
        """The proxy thread: one event loop, serving until told to stop."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._prepare_cache_dir()
        try:
            loop.run_until_complete(self._serve_forever())
        except BaseException as exc:  # pragma: no cover - defensive
            self._error = exc
            self._ready.set()
        finally:
            # Response bodies are streamed, so a stream cut off at quit leaves
            # httpx/httpcore async generators open. Closed here, on the loop
            # that owns them; left to the garbage collector they are
            # finalized after the loop is gone ("coroutine method 'aclose'
            # ... was never awaited").
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            with contextlib.suppress(Exception):
                loop.close()
            self._loop = None

    async def _serve_forever(self) -> None:
        self._stop = asyncio.Event()
        try:
            self._server = await asyncio.start_server(self._handle, host="127.0.0.1", port=0)
            self._port = self._server.sockets[0].getsockname()[1]
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            await self._stop.wait()
        finally:
            for task in list(self._handlers):
                task.cancel()
            if self._handlers:
                await asyncio.wait(list(self._handlers), timeout=_CLEANUP_TIMEOUT_S)
            if self._server is not None:
                self._server.close()
                with contextlib.suppress(Exception):
                    await self._server.wait_closed()
            # Whatever a cancelled handler could not reap, reaped here. This
            # loop is the proxy's own, so everything left on it belongs to
            # this module, and a task still pending when the loop closes is
            # printed as "Task was destroyed but it is pending" -- a screenful
            # of it, at the exact moment the app is quitting.
            leftovers = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in leftovers:
                task.cancel()
            if leftovers:
                with contextlib.suppress(Exception):
                    await asyncio.wait(leftovers, timeout=_CLEANUP_TIMEOUT_S)
                self._server = None
            # A stream's bytes do not outlive the app: its debrid token is in
            # them. (A process killed before this runs is cleaned up by the
            # next start -- see _prepare_cache_dir.)
            with self._entries_lock:
                remaining = list(self._entries.values())
                self._entries = {}
            for entry in remaining:
                entry.cache.close()
            if self._cache_dir is not None:
                shutil.rmtree(self._cache_dir, ignore_errors=True)

    def _prepare_cache_dir(self) -> None:
        """Start from an empty cache directory. What is there is a previous
        session's -- one that crashed before cleaning up -- and a debrid
        stream's bytes do not outlive their playback. Runs on the proxy thread:
        a crashed session can leave gigabytes, and deleting them is not the GUI
        thread's business. A directory that cannot be made turns the disk cache
        off, never the proxy."""
        root = self._cache_dir
        if root is None:
            return
        shutil.rmtree(root, ignore_errors=True)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _log.warning("stream proxy cache stays in memory: cannot use %s: %s", root, exc)
            self._cache_dir = None
            self._cache_bytes = CACHE_MAX_BYTES
            self._cache_behind = CACHE_MAX_BYTES // 2

    def holds_stream_cache(self) -> bool:
        """Whether a stream played through this proxy is cached on disk here,
        so the player can keep only a small cache of its own."""
        return (
            self.enabled
            and self._ready.is_set()
            and self._error is None
            and self._cache_dir is not None
        )

    def _new_cache(self, token: str) -> _ChunkCache:
        return _ChunkCache(
            self._cache_bytes,
            behind=self._cache_behind,
            directory=self._cache_dir / token if self._cache_dir is not None else None,
        )

    def _retire(self, entries: list[_Entry]) -> None:
        """Delete the caches of streams nobody plays any more -- on the proxy
        thread, where every other touch of them happens."""
        if not entries:
            return

        def close() -> None:
            for entry in entries:
                entry.cache.close()

        loop = self._loop
        if loop is None:
            close()
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(close)

    def shutdown(self) -> None:
        """Stop serving, synchronously, from a Qt slot.

        Wired to aboutToQuit. Does not join: quitting must not wait on a
        socket, and the thread is a daemon.
        """
        loop, stop = self._loop, self._stop
        if loop is None or stop is None:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(stop.set)

    async def aclose(self) -> None:
        self.shutdown()
        thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join, _CLEANUP_TIMEOUT_S * 2)
            self._thread = None

    # --- StreamAccelerator ---

    def local_url(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str:
        """The URL to hand mpv. The upstream URL itself whenever this cannot
        help -- a stopped proxy, the feature turned off, or a scheme mpv has to
        resolve on its own."""
        if not self.enabled or not self._ready.is_set() or self._error is not None:
            return url
        if not is_proxyable(url):
            return url
        with self._entries_lock:
            if url in self._broken:
                return url
        token = secrets.token_urlsafe(16)
        with self._entries_lock:
            # A new stream has not failed yet, and the reason the last one did
            # is not an answer about this one.
            self._last_failure = None
            # One stream at a time: a new playback makes every earlier token
            # dead, and keeping them would keep their URLs (which carry debrid
            # tokens) in memory for the rest of the session.
            retired = list(self._entries.values())
            self._entries = {
                token: _Entry(
                    url=url,
                    headers=tuple(headers),
                    cache=self._new_cache(token),
                    parallel=self._starting_parallel(url),
                )
            }
        self._retire(retired)
        return f"http://127.0.0.1:{self._port}/{token}"

    def _starting_parallel(self, url: str) -> int:
        """How many workers a new stream starts with: the configured count,
        or what this host was last seen to accept if that is fewer and still
        recent. Caller holds _entries_lock."""
        host = httpx.URL(url).host
        learned = self._host_limits.get(host)
        if learned is None:
            return self._connections
        limit, at = learned
        if time.monotonic() - at > _HOST_LIMIT_TTL_S:
            del self._host_limits[host]
            return self._connections
        return max(1, min(self._connections, limit))

    def upstream_bytes_per_s(self) -> float:
        """What the host is actually delivering, or 0.0 when nothing has been
        fetched in the last _RATE_WINDOW_S. Zero means "no reading", not "the
        link is idle" -- and never "ask the player", whose reading of a proxied
        stream is loopback."""
        return self._meter.bytes_per_s()

    def last_failure(self) -> str | None:
        """Why the last stream could not be reached, or None. See the port."""
        with self._entries_lock:
            return self._last_failure

    # --- server ---

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
        entry: _Entry | None = None
        try:
            request = await self._read_request(reader)
            if request is None:
                await self._send_status(writer, 400, "Bad Request")
                return
            method, path, headers = request
            with self._entries_lock:
                entry = self._entries.get(path.lstrip("/"))
            if entry is None:
                await self._send_status(writer, 404, "Not Found")
                return
            if method not in ("GET", "HEAD"):
                await self._send_status(writer, 405, "Method Not Allowed")
                return
            # One httpx client per mpv connection, not one for the proxy.
            # Cancelling a worker that is mid-`connect_tcp` can leave a slot
            # held in httpcore's pool, and a shared pool accumulates those
            # across seeks until nothing can connect at all -- which looks
            # exactly like the stall this module exists to remove. Scoped
            # here, a leaked slot dies with the connection that leaked it.
            with self._entries_lock:
                retired = entry.url in self._broken
            if retired:
                # Already given up on: do not spend another round trip
                # discovering that again.
                await self._redirect(writer, entry.url)
                return
            client = httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                limits=httpx.Limits(
                    max_connections=self._connections + 2,
                    max_keepalive_connections=self._connections + 2,
                ),
            )
            state = _ResponseState()
            try:
                await self._serve(client, entry, method, headers, reader, writer, state)
            except Exception as exc:
                if state.started:
                    raise
                # Nothing has been sent yet, so mpv can still be pointed at
                # the real URL. A 302 is the whole recovery: ffmpeg follows it
                # and reads the stream itself, in the same request, with no
                # stall and nothing for the player to notice. Closing the
                # socket instead is what left mpv reconnecting to a proxy that
                # could not serve it, forever.
                self._note_failure(entry)
                refused = _unreachable_host(exc)
                if refused is not None:
                    # ...but a redirect to a host that just refused a
                    # connection is not a recovery, it is ffmpeg rediscovering
                    # the same refusal a retry ladder later. Fail the load now
                    # so the player can try the next source while the viewer
                    # is still watching a spinner rather than a timeout.
                    reason = await _describe_unreachable(refused)
                    with self._entries_lock:
                        self._last_failure = reason
                    _log.warning(
                        "%s for %s; failing the load so the next source is tried",
                        reason,
                        abbreviate_url(entry.url),
                    )
                    await self._send_status(writer, 502, "Bad Gateway")
                    return
                _log.warning(
                    "cannot proxy %s (%r); redirecting mpv to it",
                    abbreviate_url(entry.url),
                    exc,
                )
                await self._redirect(writer, entry.url)
            finally:
                # Deadlined rather than awaited outright: closing a pool that
                # still holds a slot blocks, and the socket to mpv must be
                # closed either way -- a client left to the garbage collector
                # is a smaller problem than a handler that never returns.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(client.aclose(), timeout=_CLEANUP_TIMEOUT_S)
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass  # mpv seeked or stopped; the handler dying is the point
        except _ClientStalled:
            # Not a failure, so no traceback: measured with the native engine
            # (a 30s read-ahead) paused past the deadline, the next read after
            # resuming reconnected on its own and playback went on.
            _log.info(
                "the player stopped reading for %.0fs (paused?); closing the "
                "connection, it reconnects when it wants more",
                _WRITE_TIMEOUT_S,
            )
        except Exception as exc:
            # Warning, not debug: mpv reports a proxy that dies mid-response
            # as "Error reading HTTP response: End of file", which names the
            # symptom and hides the cause. This is the only place the cause
            # exists.
            _log.warning("proxy request failed: %r", exc, exc_info=True)
            self._note_failure(entry)
        finally:
            if task is not None:
                self._handlers.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    def _note_failure(self, entry: _Entry | None) -> None:
        """Count a failed request against its stream, and retire the stream
        once it has failed enough times."""
        if entry is None:
            return
        entry.failures += 1
        if entry.failures < _MAX_ENTRY_FAILURES:
            return
        with self._entries_lock:
            if entry.url not in self._broken:
                self._broken.append(entry.url)
                del self._broken[:-_BROKEN_LIMIT]
        _log.warning(
            "giving up on proxying %s after %d failures; it will be played directly",
            abbreviate_url(entry.url),
            entry.failures,
        )

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str]] | None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_HEADER_TIMEOUT_S)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return None
        if len(raw) > _MAX_REQUEST_BYTES:
            return None
        lines = raw.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        return parts[0].upper(), parts[1], headers

    async def _serve(
        self,
        client: httpx.AsyncClient,
        entry: _Entry,
        method: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        state: _ResponseState,
    ) -> None:
        # A probe that could not reach the host serves this one request the
        # plain way; the next connection probes again.
        with contextlib.suppress(_ProbeFailed):
            await self._probe(client, entry)
        if not entry.ranged or entry.size is None:
            # The host will not serve ranges, or will not say how big the file
            # is. Either way there is nothing to split, so this becomes a plain
            # copy: exactly what mpv would have done by itself.
            await self._passthrough(client, entry, method, headers, writer, state)
            return

        size = entry.size
        wanted = parse_range(headers.get("range", ""), size)
        if headers.get("range") and wanted is None:
            await self._send_status(writer, 416, "Range Not Satisfiable")
            return
        first, last = wanted if wanted is not None else (0, size - 1)
        length = last - first + 1
        response = [
            f"HTTP/1.1 {'206 Partial Content' if wanted is not None else '200 OK'}",
            "Content-Type: application/octet-stream",
            f"Content-Length: {length}",
            "Accept-Ranges: bytes",
            "Connection: close",
        ]
        if wanted is not None:
            response.append(f"Content-Range: bytes {first}-{last}/{size}")
        state.started = True
        writer.write(("\r\n".join(response) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        if method == "HEAD":
            return
        await self._pump(client, entry, first, last, reader, writer)

    async def _probe(self, client: httpx.AsyncClient, entry: _Entry) -> None:
        """Ask the host, once per stream, whether it serves ranges and how big
        the file is. Both answers come from a single one-byte range request:
        a 206 with a `Content-Range` says yes and states the total."""
        if entry.ranged is not None:
            return
        async with entry.probe_lock:
            if entry.ranged is not None:
                return
            try:
                response = await self._probe_request(client, entry)
            except _ProbeFailed:
                raise
            except Exception as exc:
                # Deliberately NOT recorded as "this host has no ranges".
                # A refused or timed-out probe is a fact about one moment, not
                # about the host, and caching it turned one bad request into a
                # stream that could never be split again -- every later
                # connection went straight to the passthrough that had just
                # failed, and mpv saw an instant end of file each time.
                _log.warning(
                    "proxy probe failed for %s: %r -- one connection for this request",
                    abbreviate_url(entry.url),
                    exc,
                )
                raise _ProbeFailed from exc
            total = _total_from_content_range(response.headers.get("content-range", ""))
            with self._entries_lock:
                self._last_failure = None  # the host answered
            entry.ranged = response.status_code == 206 and total is not None
            entry.size = total
            # Where the redirects ended. An addon URL here is a signing
            # endpoint that 302s to a CDN, and walking that chain again per
            # block is a round trip per block against the slowest hop in it.
            entry.resolved = str(response.url)
            _log.info(
                "proxying %s: ranges %s, %s bytes",
                abbreviate_url(entry.url),
                "yes" if entry.ranged else "no",
                total if total is not None else "unknown",
            )

    async def _probe_request(self, client: httpx.AsyncClient, entry: _Entry) -> httpx.Response:
        """The probe's one-byte request, waiting out a rate limit.

        A 429 is not a 206, and was recorded as "this host has no ranges":
        cached for the stream, so every later connection went through the
        passthrough, which handed mpv the 429 itself. mpv failed to open, its
        reconnects re-probed into more 429s, and the one that finally opened
        could not seek to the file's index and landed at the very end of the
        episode (measured: switching episodes on a TorBox account that was
        already throttling). A rate limit is the host asking for patience, so
        the probe waits the way a block does, and a limit that outlasts the
        wait is a failed probe -- never a fact about the host.
        """
        for attempt in range(_PROBE_RATE_LIMIT_ATTEMPTS + 1):
            response = await client.get(
                entry.url,
                headers={**dict(entry.headers), "Range": "bytes=0-0"},
            )
            await response.aclose()
            if response.status_code not in _RATE_LIMIT_STATUSES:
                return response
            if response.status_code == 503 and _retry_after_s(response) is None:
                return response  # a plain 503 is an outage, not a limit
            self._slow_down(entry)
            if attempt == _PROBE_RATE_LIMIT_ATTEMPTS:
                break
            step = _RATE_LIMIT_DELAYS_S[min(attempt, len(_RATE_LIMIT_DELAYS_S) - 1)]
            await asyncio.sleep(max(_retry_after_s(response) or 0.0, step))
        _log.warning(
            "proxy probe for %s still rate limited after %d tries -- one connection "
            "for this request",
            abbreviate_url(entry.url),
            _PROBE_RATE_LIMIT_ATTEMPTS + 1,
        )
        raise _ProbeFailed(f"rate limited ({response.status_code})")

    async def _passthrough(
        self,
        client: httpx.AsyncClient,
        entry: _Entry,
        method: str,
        headers: dict[str, str],
        writer: asyncio.StreamWriter,
        state: _ResponseState,
    ) -> None:
        """One connection, copied straight through. The fallback, and the
        reason a host this cannot accelerate is never a host it breaks."""
        upstream_headers = dict(entry.headers)
        if "range" in headers:
            upstream_headers["Range"] = headers["range"]
        async with client.stream(method, entry.url, headers=upstream_headers) as response:
            head = [f"HTTP/1.1 {response.status_code} Upstream", "Connection: close"]
            for name in ("content-type", "content-length", "content-range", "accept-ranges"):
                value = response.headers.get(name)
                if value:
                    head.append(f"{name}: {value}")
            state.started = True
            writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            await writer.drain()
            if method == "HEAD":
                return
            async for chunk in response.aiter_bytes():
                self._meter.add(len(chunk))
                writer.write(chunk)
                await asyncio.wait_for(writer.drain(), timeout=_WRITE_TIMEOUT_S)

    async def _pump(
        self,
        client: httpx.AsyncClient,
        entry: _Entry,
        first: int,
        last: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Serve `first..last` from the stream's chunk cache, filling it as
        needed over `self._connections` workers plus one backfill worker.

        The writer walks the chunk grid in order and waits on the cache; the
        workers race ahead filling it, bounded by READAHEAD_CHUNKS. Anything
        already cached -- played a minute ago, or pulled in behind the
        starting position by the backfill -- is written straight out, which is
        what makes a seek backwards and then forwards again cost nothing.

        The writer runs as a task raced against a watcher on the client
        socket, and that shape is the whole point. mpv ends a connection by
        closing it -- every seek does -- and a `drain()` to a closed peer can
        block indefinitely rather than raise, so a writer waiting inside one
        never reaches its own cleanup. Racing the two means the close is
        noticed by the side that cannot block.
        """
        first_chunk = first // self._chunk_bytes
        last_chunk = last // self._chunk_bytes
        # Never read further ahead than the cache can hold: a run landing
        # faster than mpv reads it would otherwise push out the very chunks
        # the writer is about to send.
        # In memory, a short window past what mpv is reading. On disk the proxy
        # is the stream's main cache, and its read-ahead is what carries
        # playback through a network drop -- the job mpv's own gigabytes of
        # read-ahead did before -- so it reads as far ahead as its share of
        # the cache allows.
        ahead_chunks = (self._cache_bytes - self._cache_behind) // self._chunk_bytes
        window = ahead_chunks if self._cache_dir is not None else READAHEAD_CHUNKS
        readahead = max(1, min(window, self._cache_bytes // self._chunk_bytes - 1))
        entry.cache.anchor = first_chunk
        cursor = first_chunk
        cursor_lock = asyncio.Lock()
        position_chunk = first_chunk
        failure: list[BaseException] = []
        stopping = False
        # Set while the writer is parked in drain(), which means mpv is not
        # taking bytes -- its own cache is full. That is exactly the spare
        # bandwidth the backfill is allowed to use, and the only time it is.
        spare = asyncio.Event()

        async def claim(index: int) -> bool:
            """Take responsibility for fetching this chunk, or report that
            somebody already has."""
            async with cursor_lock:
                if index in entry.cache or index in entry.inflight:
                    return False
                entry.inflight.add(index)
                return True

        def release(index: int) -> None:
            entry.inflight.discard(index)
            entry.landed.set()

        async def fetch(run: list[int]) -> None:
            """Fetch a claimed run of consecutive chunks in one request. Each
            chunk is cached and released the moment it is complete; whatever
            the run did not deliver is released on the way out, so another
            worker (or the next connection) can claim it."""
            delivered: set[int] = set()

            def deliver(index: int, data: bytes) -> None:
                entry.cache.put(index, data)
                delivered.add(index)
                release(index)

            try:
                await self._fetch_run(client, entry, run, deliver, lambda: stopping)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure.append(exc)
            finally:
                for index in run:
                    if index not in delivered:
                        release(index)

        async def forward(worker: int) -> None:
            nonlocal cursor
            while not stopping and not failure:
                if worker >= entry.parallel:
                    # The host asked for fewer readers while this one was
                    # running (see _slow_down). Stand down; the workers below
                    # the new limit carry the stream on their own.
                    return
                run: list[int] | None
                async with cursor_lock:
                    if cursor > last_chunk:
                        return
                    ahead = cursor - position_chunk
                    room = readahead - ahead
                    # Wait for room for a whole run while plenty is buffered.
                    # Claiming each slot as it frees would turn steady play
                    # back into one request per chunk (measured: 4-6/s at a
                    # full window). Short runs are only worth it when the
                    # writer is about to run dry.
                    whole_run = min(RUN_CHUNKS, readahead)
                    if room <= 0 or (room < whole_run and ahead > whole_run):
                        run = None
                    else:
                        # The next stretch nobody holds: cached chunks at the
                        # front are stepped over, and the run ends at the
                        # first chunk that is cached or being fetched already.
                        run = []
                        while cursor <= last_chunk and len(run) < min(RUN_CHUNKS, room):
                            index = cursor
                            if index in entry.cache or index in entry.inflight:
                                if run:
                                    break
                                cursor += 1
                                continue
                            entry.inflight.add(index)
                            run.append(index)
                            cursor += 1
                if run is None:
                    # The window is full: mpv has not caught up yet. Wait for
                    # it rather than reading on into memory nobody wants.
                    entry.landed.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(entry.landed.wait(), timeout=0.5)
                    continue
                if run:
                    await fetch(run)

        async def backfill() -> None:
            """Fetch the chunks BEFORE the starting position, on spare
            bandwidth only.

            Resuming at 3:51 means nothing before 3:51 has ever been
            downloaded, so stepping back a few seconds is a cold fetch. This
            goes and gets it while mpv is not asking for anything, and stops
            the moment mpv is.
            """
            if entry.backfill_next < 0:
                entry.backfill_next = first_chunk - 1
                entry.backfill_floor = max(
                    0, first_chunk - self._backfill_bytes // self._chunk_bytes
                )
            while not stopping and not failure:
                await spare.wait()
                async with cursor_lock:
                    if entry.backfill_next < entry.backfill_floor:
                        return
                    # A run downwards from where the backfill has reached,
                    # fetched as one ascending request.
                    run: list[int] = []
                    while len(run) < RUN_CHUNKS and entry.backfill_next >= entry.backfill_floor:
                        index = entry.backfill_next
                        if index in entry.cache or index in entry.inflight:
                            if run:
                                break
                            entry.backfill_next -= 1
                            continue
                        entry.inflight.add(index)
                        run.append(index)
                        entry.backfill_next -= 1
                if run:
                    await fetch(sorted(run))

        async def write_out() -> None:
            nonlocal position_chunk, cursor
            position = first
            while position <= last:
                index = position // self._chunk_bytes
                chunk = entry.cache.get(index)
                if chunk is None:
                    if failure:
                        raise failure[0]
                    async with cursor_lock:
                        # Nobody is fetching it and the workers have moved on:
                        # it was evicted before it could be sent, or dropped
                        # by a run that stood down. Send them back for it --
                        # waiting on its arrival would be waiting forever.
                        if index not in entry.inflight and index < cursor:
                            cursor = index
                    entry.landed.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(entry.landed.wait(), timeout=0.5)
                    continue
                position_chunk = index
                entry.cache.anchor = index
                base = index * self._chunk_bytes
                stop = min(last, base + len(chunk) - 1)
                view = memoryview(chunk)[position - base : stop - base + 1]
                position = stop + 1
                for offset in range(0, len(view), _WRITE_SLICE_BYTES):
                    writer.write(view[offset : offset + _WRITE_SLICE_BYTES])
                    # Deadlined for the same reason the race below exists: a
                    # drain() to a peer that has gone away may never return.
                    spare.set()
                    try:
                        await asyncio.wait_for(writer.drain(), timeout=_WRITE_TIMEOUT_S)
                    except TimeoutError as exc:
                        raise _ClientStalled from exc
                    finally:
                        spare.clear()

        async def watch_close() -> None:
            """Return as soon as mpv closes its end. Every response here says
            `Connection: close`, so anything arriving on this socket is the
            end of it, never another request."""
            with contextlib.suppress(Exception):
                await reader.read()

        tasks = [asyncio.create_task(forward(worker)) for worker in range(entry.parallel)]
        tasks.append(asyncio.create_task(backfill()))
        writing = asyncio.create_task(write_out())
        closed = asyncio.create_task(watch_close())
        try:
            await asyncio.wait({writing, closed}, return_when=asyncio.FIRST_COMPLETED)
            if writing.done():
                await writing  # surface a real failure to the handler
        finally:
            # Two stages, and the order matters. First ask: `stopping` plus a
            # wake wherever a worker might be parked, and a worker mid-request
            # finishes that request and returns -- no cancellation reaches
            # httpx, so no pool slot is left held and the client can be
            # closed. Only what is still running after the grace is cancelled.
            stopping = True
            entry.landed.set()
            spare.set()
            writing.cancel()  # no upstream request of its own to protect
            closed.cancel()
            everything = [*tasks, writing, closed]
            try:
                await asyncio.wait(tasks, timeout=_GRACE_TIMEOUT_S)
            finally:
                # In a nested finally because the grace above is an await, and
                # an await in a teardown can itself be cancelled -- which is
                # what happens when the app quits while a handler is here.
                # Skipping the cancellation then leaves the workers pending on
                # a loop about to close, and they surface as a screenful of
                # "Task was destroyed but it is pending". `cancel()` needs no
                # await, so it survives that.
                stragglers = [task for task in everything if not task.done()]
                for task in stragglers:
                    task.cancel()
                if stragglers:
                    # Bounded: the handler cannot return until cleanup does,
                    # and a handler that never returns is exactly the leak
                    # this whole shape exists to prevent.
                    with contextlib.suppress(asyncio.CancelledError):
                        await asyncio.wait(stragglers, timeout=_CLEANUP_TIMEOUT_S)
                for task in everything:
                    # Retrieve, so a failed chunk does not resurface later as
                    # "exception was never retrieved" from an unrelated turn.
                    if task.done() and not task.cancelled():
                        with contextlib.suppress(BaseException):
                            task.exception()

    def _chunk_length(self, entry: _Entry, index: int) -> int:
        if entry.size is None:
            return self._chunk_bytes
        return max(0, min(self._chunk_bytes, entry.size - index * self._chunk_bytes))

    async def _fetch_run(
        self,
        client: httpx.AsyncClient,
        entry: _Entry,
        run: list[int],
        deliver: Callable[[int, bytes], None],
        should_stop: Callable[[], bool],
    ) -> None:
        """Fetch consecutive chunks `run` in one ranged request, handing each to
        `deliver` the moment it is complete. Retried a few times, resuming at
        the first chunk not yet delivered. A run that will not come is the end
        of this connection: mpv reads that as end of file, and
        PlayerController's stall recovery reloads from the frozen position.

        `should_stop` is checked between pieces of the body: a teardown asks,
        and a worker that is streaming a run finishes the piece in hand and
        returns rather than being cancelled mid-request (see _pump)."""
        pending = list(run)
        last_error: Exception | None = None
        # Two budgets, because these are two different events. A block that
        # *fails* is retried a couple of times and then ends the connection. A
        # block the host has only refused to serve *yet* is not a failure at
        # all, and spending the failure budget on it is how a rate limit
        # became a dead playback.
        failures = 0
        throttled = 0
        while pending and failures < _BLOCK_ATTEMPTS and throttled <= _RATE_LIMIT_ATTEMPTS:
            if should_stop():
                return
            start = pending[0] * self._chunk_bytes
            end = (pending[-1] + 1) * self._chunk_bytes - 1
            if entry.size is not None:
                end = min(end, entry.size - 1)
            headers = {**dict(entry.headers), "Range": f"bytes={start}-{end}"}
            # The resolved URL first, the original as the fallback: a CDN URL
            # can expire or be single-use, and the signer will hand out
            # another one.
            first_try = failures + throttled == 0
            url = entry.resolved if (entry.resolved and first_try) else entry.url
            progressed = False
            try:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code in _RATE_LIMIT_STATUSES:
                        raise _RateLimited(_retry_after_s(response))
                    # 200 answers a range with the whole file from byte 0,
                    # which is only the right bytes if byte 0 was asked for.
                    if response.status_code != 206 and not (
                        response.status_code == 200 and start == 0
                    ):
                        raise OSError(f"upstream returned HTTP {response.status_code}")
                    buffer = bytearray()
                    # Closed deterministically, cancellation included: an
                    # async generator abandoned mid-body is otherwise closed
                    # later by the loop -- at quit, on a loop that is going
                    # away, as "Task was destroyed but it is pending".
                    # aiter_bytes is typed as an iterator; it is a generator,
                    # which is what gives it the aclose() this relies on.
                    body = cast(
                        AsyncGenerator[bytes, None], response.aiter_bytes(_READ_PIECE_BYTES)
                    )
                    async with contextlib.aclosing(body) as pieces:
                        async for piece in pieces:
                            self._meter.add(len(piece))
                            buffer += piece
                            while pending:
                                need = self._chunk_length(entry, pending[0])
                                if len(buffer) < need:
                                    break
                                deliver(pending.pop(0), bytes(buffer[:need]))
                                del buffer[:need]
                                progressed = True
                            if not pending or should_stop():
                                break
                    if pending and not should_stop():
                        raise OSError("upstream ended before the range did")
            except asyncio.CancelledError:
                raise
            except _RateLimited as exc:
                # The host is asking for fewer readers. Give it fewer readers
                # -- for this stream and for every later connection to it --
                # and wait out the pause it named.
                self._slow_down(entry)
                last_error = exc
                # `Retry-After` is a floor, not the whole answer. A host that
                # says 0 is not saying "ask again now" in any useful sense --
                # asking again now is what just failed, and a worker that
                # spends its whole budget inside a millisecond never gives the
                # stream time to stand its extra readers down.
                step = _RATE_LIMIT_DELAYS_S[min(throttled, len(_RATE_LIMIT_DELAYS_S) - 1)]
                delay = max(exc.after or 0.0, step)
                throttled += 1
                if throttled > _RATE_LIMIT_ATTEMPTS:
                    break
                await asyncio.sleep(delay)
            except Exception as exc:
                last_error = exc
                # A run that delivered chunks before it broke made progress;
                # only a request that got nowhere spends the failure budget.
                failures = 0 if progressed else failures + 1
                if failures < _BLOCK_ATTEMPTS and not should_stop():
                    await asyncio.sleep(_RETRY_DELAY_S * max(1, failures))
        if pending and not should_stop():
            raise OSError(f"chunks {pending[0]}-{pending[-1]} failed: {last_error}")

    def _slow_down(self, entry: _Entry) -> None:
        """One fewer reader on this stream, floored at one.

        At one the proxy is doing exactly what mpv's own HTTP client would do
        -- a single connection, read front to back -- which is the floor this
        whole module promises never to fall below. It does not go back up:
        nothing here can tell "the limit has lifted" from "the limit is about
        to be hit again", and the cost of guessing wrong is another stall.
        """
        now = time.monotonic()
        if entry.parallel <= 1 or now - entry.shrunk_at < _SHRINK_DEBOUNCE_S:
            return
        entry.shrunk_at = now
        entry.parallel -= 1
        # Remembered for the host, so the next stream from it -- a new token,
        # the next episode -- does not start above the limit it just hit.
        with self._entries_lock:
            self._host_limits[httpx.URL(entry.url).host] = (entry.parallel, now)
        _log.warning(
            "%s is rate limiting; dropping to %d connection%s for this stream",
            abbreviate_url(entry.url),
            entry.parallel,
            "" if entry.parallel == 1 else "s",
        )

    async def _redirect(self, writer: asyncio.StreamWriter, url: str) -> None:
        """Send mpv to the real URL. ffmpeg follows a 302 by itself, so this
        turns "the proxy cannot serve this" into "the proxy is not in the
        path" without the player ever seeing an error."""
        # A header cannot carry a newline, and a stream URL is attacker-shaped
        # input as far as this server is concerned.
        location = url.replace("\r", "").replace("\n", "")
        writer.write(
            (
                "HTTP/1.1 302 Found\r\n"
                f"Location: {location}\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1")
        )
        with contextlib.suppress(Exception):
            await writer.drain()

    async def _send_status(self, writer: asyncio.StreamWriter, code: int, text: str) -> None:
        writer.write(
            f"HTTP/1.1 {code} {text}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )
        with contextlib.suppress(Exception):
            await writer.drain()


def _total_from_content_range(value: str) -> int | None:
    """The total size out of `bytes 0-0/12345`. None for `*` (a host that
    serves ranges but will not say how long the file is), which cannot be
    split into blocks."""
    _, _, total = value.partition("/")
    total = total.strip()
    if not total.isdigit():
        return None
    size = int(total)
    return size if size > 0 else None
