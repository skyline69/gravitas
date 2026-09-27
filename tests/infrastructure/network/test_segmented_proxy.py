"""The multi-connection stream proxy, against a real loopback origin.

A fake httpx transport would prove the block arithmetic and nothing else: the
parts that can actually break here are the HTTP the proxy *speaks* to mpv, and
what it does when the origin behaves badly. So these run a small origin server
on 127.0.0.1 and talk to the proxy over a socket, the way mpv does.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
from collections.abc import AsyncIterator
from itertools import pairwise
from pathlib import Path

import pytest

from gravitas.infrastructure.network.segmented_proxy import (
    _MAX_ENTRY_FAILURES,
    SegmentedStreamProxy,
    _describe_unreachable,
    is_proxyable,
    parse_range,
)

BODY = bytes((i * 7 + 11) % 251 for i in range(3 * 1024 * 1024))


class Origin:
    """A minimal HTTP origin. Counts the ranged requests it served, which is
    the only way to prove the fetch really was split."""

    def __init__(
        self,
        *,
        ranges: bool = True,
        body: bytes = BODY,
        delay_s: float = 0.0,
        refuse: int = 0,
        concurrency: int = 0,
        limit_first: int = 0,
    ) -> None:
        self.ranges = ranges
        # Answer 429 to the first this-many requests, whatever they are: an
        # account the CDN is already throttling when playback starts.
        self.limit_first = limit_first
        self.body = body
        self.delay_s = delay_s
        # Seconds between 256 KiB pieces of a body: a real link delivers over
        # time, and one burst of bytes is no rate at all.
        self.trickle_s = 0.0
        # Drop this many connections without answering, then behave.
        self.refuse = refuse
        # Answer 429 to anything past this many requests at once, the way a
        # debrid CDN shapes a single account. 0 means no limit.
        self.concurrency = concurrency
        self.inflight = 0
        self.peak_inflight = 0
        self.rate_limited = 0
        self.range_requests: list[tuple[int, int]] = []
        self.headers_seen: list[dict[str, str]] = []
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/file.mkv"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.readuntil(b"\r\n\r\n")
        except Exception:
            return
        lines = raw.decode("latin-1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        if self.refuse > 0:
            self.refuse -= 1
            writer.close()
            return
        if self.refuse > 0:
            self.refuse -= 1
            writer.close()
            return
        self.headers_seen.append(headers)
        self.inflight += 1
        self.peak_inflight = max(self.peak_inflight, self.inflight)
        try:
            limited_now = self.limit_first > 0
            if limited_now:
                self.limit_first -= 1
            if limited_now or (self.concurrency and self.inflight > self.concurrency):
                self.rate_limited += 1
                writer.write(
                    b"HTTP/1.1 429 Too Many Requests\r\n"
                    b"Retry-After: 0\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                with contextlib.suppress(Exception):
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                return
            await self._respond(headers, writer)
        finally:
            self.inflight -= 1

    async def _respond(self, headers: dict[str, str], writer: asyncio.StreamWriter) -> None:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        span = headers.get("range", "")
        if self.ranges and span.startswith("bytes="):
            first_text, _, last_text = span[6:].partition("-")
            first = int(first_text)
            last = int(last_text) if last_text else len(self.body) - 1
            self.range_requests.append((first, last))
            chunk = self.body[first : last + 1]
            head = (
                "HTTP/1.1 206 Partial Content\r\n"
                f"Content-Length: {len(chunk)}\r\n"
                f"Content-Range: bytes {first}-{last}/{len(self.body)}\r\n"
                "Connection: close\r\n\r\n"
            )
        else:
            chunk = self.body
            head = f"HTTP/1.1 200 OK\r\nContent-Length: {len(chunk)}\r\nConnection: close\r\n\r\n"
        with contextlib.suppress(Exception):
            if self.trickle_s:
                writer.write(head.encode("latin-1"))
                for offset in range(0, len(chunk), 256 * 1024):
                    writer.write(chunk[offset : offset + 256 * 1024])
                    await writer.drain()
                    await asyncio.sleep(self.trickle_s)
            else:
                writer.write(head.encode("latin-1") + chunk)
            await writer.drain()
            writer.close()
            await writer.wait_closed()


class Redirector:
    """A signing endpoint: 302s to the real origin, and counts how often it is
    asked. Addon stream URLs behave exactly like this."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.hits = 0
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/sign"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await reader.readuntil(b"\r\n\r\n")
        self.hits += 1
        writer.write(
            (
                "HTTP/1.1 302 Found\r\n"
                f"Location: {self.target}\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1")
        )
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()
            await writer.wait_closed()


async def fetch(url: str, *, span: str | None = None) -> tuple[int, dict[str, str], bytes]:
    """One HTTP/1.1 GET over a raw socket, as ffmpeg would issue it."""
    _, _, rest = url.partition("http://")
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    reader, writer = await asyncio.open_connection(host, int(port))
    request = f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
    if span is not None:
        request += f"Range: {span}\r\n"
    writer.write((request + "Connection: close\r\n\r\n").encode("latin-1"))
    await writer.drain()
    raw = await reader.readuntil(b"\r\n\r\n")
    lines = raw.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    body = await reader.read()
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return status, headers, body


@pytest.fixture
async def origin() -> AsyncIterator[Origin]:
    server = Origin()
    await server.start()
    try:
        yield server
    finally:
        await server.aclose()


@pytest.fixture
async def proxy() -> AsyncIterator[SegmentedStreamProxy]:
    # A small block size so a 3 MiB fixture is genuinely split; the real
    # default is 4 MiB, which would make this whole file one block.
    accelerator = SegmentedStreamProxy(connections=4, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        yield accelerator
    finally:
        await accelerator.aclose()


async def test_the_whole_file_comes_back_byte_for_byte(
    origin: Origin, proxy: SegmentedStreamProxy
) -> None:
    status, headers, body = await fetch(proxy.local_url(origin.url))
    assert status == 200
    assert body == BODY
    assert headers["content-length"] == str(len(BODY))
    assert headers["accept-ranges"] == "bytes"


async def test_the_file_is_actually_fetched_in_parallel_blocks(
    origin: Origin, proxy: SegmentedStreamProxy
) -> None:
    await fetch(proxy.local_url(origin.url))
    # One probe plus one request per block: a single sequential read would be
    # two requests in total, so this is the whole point of the module.
    assert len(origin.range_requests) > 2
    fetched = sorted(origin.range_requests)[1:]  # drop the 0-0 probe
    # Every byte exactly once, in blocks, with no overlap and no gap.
    assert fetched[0][0] == 0
    for (_, prev_last), (next_first, _) in pairwise(fetched):
        assert next_first == prev_last + 1
    assert fetched[-1][1] == len(BODY) - 1


async def test_a_range_request_is_answered_as_a_range(
    origin: Origin, proxy: SegmentedStreamProxy
) -> None:
    start = 1_000_000
    status, headers, body = await fetch(proxy.local_url(origin.url), span=f"bytes={start}-")
    assert status == 206
    assert headers["content-range"] == f"bytes {start}-{len(BODY) - 1}/{len(BODY)}"
    assert body == BODY[start:]


async def test_a_bounded_range_returns_exactly_that_slice(
    origin: Origin, proxy: SegmentedStreamProxy
) -> None:
    status, _, body = await fetch(proxy.local_url(origin.url), span="bytes=100-199")
    assert status == 206
    assert body == BODY[100:200]


async def test_the_stream_headers_reach_the_origin(
    origin: Origin, proxy: SegmentedStreamProxy
) -> None:
    # behaviorHints.proxyHeaders: some addons 403 without their Referer.
    url = proxy.local_url(origin.url, (("Referer", "https://addon.test/"),))
    await fetch(url)
    assert all(seen.get("referer") == "https://addon.test/" for seen in origin.headers_seen)


async def test_a_host_that_ignores_ranges_falls_back_to_one_connection() -> None:
    origin = Origin(ranges=False)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=4, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        status, _, body = await fetch(accelerator.local_url(origin.url))
        assert status == 200
        assert body == BODY  # no worse than mpv reading it directly
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_upstream_bytes_are_what_gets_measured(proxy: SegmentedStreamProxy) -> None:
    """The rate reported is the one taken off the HOST. With a proxy in the
    path mpv's own cache-speed measures loopback, which is not a fact about
    anybody's internet, so this is what the connection estimate samples."""
    assert proxy.upstream_bytes_per_s() == 0.0  # nothing proxied yet
    # Slow enough that the transfer spans a measurable stretch of time; over
    # bare loopback the whole fixture moves in a few milliseconds, which is
    # scheduling noise rather than a throughput reading.
    origin = Origin(delay_s=0.05)
    origin.trickle_s = 0.01
    await origin.start()
    try:
        await fetch(proxy.local_url(origin.url))
        assert proxy.upstream_bytes_per_s() > 0.0
    finally:
        await origin.aclose()


async def test_a_stopped_or_disabled_proxy_hands_the_url_straight_back(
    origin: Origin,
) -> None:
    unstarted = SegmentedStreamProxy()
    assert unstarted.local_url(origin.url) == origin.url

    accelerator = SegmentedStreamProxy()
    await accelerator.start()
    try:
        accelerator.enabled = False
        assert accelerator.local_url(origin.url) == origin.url
    finally:
        await accelerator.aclose()


async def test_only_http_urls_are_taken_over(proxy: SegmentedStreamProxy) -> None:
    # A ytId stream becomes a YouTube watch URL that mpv resolves through
    # ytdl_hook into an EDL of its own; standing in front of that breaks it.
    assert is_proxyable("https://host/f.mkv") is True
    assert is_proxyable("ytdl://abc") is False
    assert is_proxyable("/home/me/film.mkv") is False
    assert proxy.local_url("/home/me/film.mkv") == "/home/me/film.mkv"


async def test_an_unknown_token_is_a_404(proxy: SegmentedStreamProxy) -> None:
    proxy.local_url("https://host/f.mkv")  # establish the port
    base = proxy.local_url("https://host/f.mkv").rsplit("/", 1)[0]
    status, _, _ = await fetch(f"{base}/nonsense")
    assert status == 404


async def test_the_previous_stream_stops_being_addressable(
    origin: Origin, proxy: SegmentedStreamProxy
) -> None:
    """A registration holds a URL carrying a debrid token. Only the stream
    being played needs to be addressable, so starting another retires it."""
    first = proxy.local_url(origin.url)
    proxy.local_url(origin.url)
    status, _, _ = await fetch(first)
    assert status == 404


def test_range_header_parsing() -> None:
    assert parse_range("bytes=0-99", 1000) == (0, 99)
    assert parse_range("bytes=500-", 1000) == (500, 999)
    assert parse_range("bytes=-200", 1000) == (800, 999)
    # Past the end, malformed, multipart, and a suffix range with no size.
    assert parse_range("bytes=2000-", 1000) is None
    assert parse_range("bytes=abc-", 1000) is None
    assert parse_range("bytes=0-10,20-30", 1000) is None
    assert parse_range("bytes=-200", None) is None
    assert parse_range("", 1000) is None


async def test_a_client_that_walks_away_takes_its_workers_with_it() -> None:
    """The bug that broke the first real playback.

    mpv ends a connection by closing it, and every seek does. A `drain()` to a
    closed peer can block instead of raising, so the writer never reached its
    cleanup, its six workers stayed parked on the memory semaphore holding
    upstream connections, and the next playback starved on the connection
    pool. Two abandoned connections were enough.
    """
    origin = Origin(delay_s=0.02)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=4, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        for _ in range(3):
            url = accelerator.local_url(origin.url)
            _, _, rest = url.partition("http://")
            hostport, _, path = rest.partition("/")
            host, _, port = hostport.partition(":")
            reader, writer = await asyncio.open_connection(host, int(port))
            writer.write(
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            # Read one chunk, then walk away mid-stream, exactly as a seek does.
            await reader.read(1024)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            await asyncio.sleep(0.15)
            # The handler is gone, which is what releases its workers and
            # their upstream connections. Asserted directly rather than
            # inferred from the next request succeeding: with a large enough
            # pool a leak takes several playbacks to show as a stall.
            assert accelerator._handlers == set()

        # And a fresh connection is served in full rather than starving
        # behind the dead ones.
        status, _, body = await fetch(accelerator.local_url(origin.url))
        assert status == 200
        assert body == BODY
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_player_that_stops_reading_is_let_go_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A paused player with its buffer full stops reading without closing.
    Past the write deadline the proxy ends the connection -- the player
    reconnects when it wants more -- and says so at info, not as a failure
    with a traceback: measured with the native engine, that is every pause
    longer than 30 seconds."""
    from gravitas.infrastructure.network import segmented_proxy

    monkeypatch.setattr(segmented_proxy, "_WRITE_TIMEOUT_S", 0.3)
    origin = Origin(body=bytes(32 * 1024 * 1024))
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=1024 * 1024)
    await accelerator.start()
    caplog.set_level(logging.INFO, logger=segmented_proxy.__name__)
    try:
        url = accelerator.local_url(origin.url)
        _, _, rest = url.partition("http://")
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        reader, writer = await asyncio.open_connection(host, int(port))
        writer.write(
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        await reader.readuntil(b"\r\n\r\n")
        await reader.read(1024)
        # ...and then nothing: paused, socket open.
        for _ in range(100):
            if not accelerator._handlers:
                break
            await asyncio.sleep(0.05)
        assert accelerator._handlers == set()
        stalled = [r for r in caplog.records if "stopped reading" in r.getMessage()]
        assert stalled and stalled[0].levelno == logging.INFO
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)
        writer.close()
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_fetches_are_aligned_to_the_chunk_grid() -> None:
    """Alignment is what makes a chunk reusable. A range starting anywhere
    inside a chunk has to hit the same cache entry, so requests are cut to the
    grid rather than to wherever the client happened to ask from."""
    origin = Origin()
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        await fetch(accelerator.local_url(origin.url), span="bytes=300000-")
        chunks = sorted(origin.range_requests)[1:]  # drop the 0-0 probe
        assert all(first % (256 * 1024) == 0 for first, _ in chunks)
        # The request started at 300000, inside chunk 1. Chunk 0 is fetched
        # too, but by the backfill: it is behind the starting position.
        assert 256 * 1024 in {first for first, _ in chunks}
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_blocks_are_fetched_from_the_url_the_redirects_resolved_to() -> None:
    """An addon stream URL is a signing endpoint that 302s to a CDN. Walking
    that chain again per block is a round trip per block against the slowest
    hop in it."""
    origin = Origin()
    await origin.start()
    signer = Redirector(origin.url)
    await signer.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        status, _, body = await fetch(accelerator.local_url(signer.url))
        assert status == 200
        assert body == BODY
        # One redirect walked, for the probe. Every block went straight to the
        # resolved URL.
        assert signer.hits == 1
    finally:
        await accelerator.aclose()
        await signer.aclose()
        await origin.aclose()


async def test_shutdown_cancels_handlers_without_awaiting_them() -> None:
    """Wired to aboutToQuit, from a Qt slot that cannot await."""
    origin = Origin(delay_s=0.05)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        task = asyncio.create_task(fetch(url))
        await asyncio.sleep(0.1)
        accelerator.shutdown()
        await asyncio.sleep(0.05)
        assert not any(not t.done() and not t.cancelled() for t in accelerator._handlers)
        task.cancel()
        with contextlib.suppress(Exception):
            await task
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_teardown_lets_workers_finish_rather_than_cancelling_them() -> None:
    """Cancelling an httpx request that is mid-`connect_tcp` can leave a slot
    held in httpcore's pool, and closing a client with a held slot blocks --
    which turned the handler's own cleanup into the hang it was written to
    prevent. Teardown therefore asks first and cancels only stragglers.

    The proof is that a connection abandoned over and over leaves the proxy
    able to serve, in full, every time: a wedged pool shows up as a request
    that simply never completes.
    """
    origin = Origin(delay_s=0.01)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=4, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        for _ in range(5):
            url = accelerator.local_url(origin.url)
            _, _, rest = url.partition("http://")
            hostport, _, path = rest.partition("/")
            host, _, port = hostport.partition(":")
            reader, writer = await asyncio.open_connection(host, int(port))
            writer.write(
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            writer.close()  # abandoned while its workers are still connecting
            with contextlib.suppress(Exception):
                await writer.wait_closed()

            status, _, body = await asyncio.wait_for(
                fetch(accelerator.local_url(origin.url)), timeout=20
            )
            assert status == 200
            assert body == BODY
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_the_proxy_does_not_run_on_the_callers_event_loop() -> None:
    """qasync makes the app's asyncio loop Qt's event loop, so a coroutine
    scheduled on it runs on the GUI thread -- next to QML animation and scene
    graph sync. Serving 25 Mbps from there lands as a hitch in the video about
    once a second."""
    accelerator = SegmentedStreamProxy()
    await accelerator.start()
    try:
        assert accelerator._loop is not asyncio.get_running_loop()
        assert accelerator._thread is not None
        assert accelerator._thread.is_alive()
        assert accelerator._thread.daemon
    finally:
        await accelerator.aclose()


async def test_resuming_midway_also_fetches_what_came_before() -> None:
    """Resuming at 3:51 means nothing before 3:51 has ever been downloaded, so
    stepping back is a cold fetch unless something goes and gets it."""
    origin = Origin()
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        start = 2 * 1024 * 1024  # chunk 8 of the 3 MiB fixture
        await fetch(accelerator.local_url(origin.url), span=f"bytes={start}-")
        # Every byte behind the starting position was asked for -- in runs of
        # chunks, so by coverage rather than by one request per chunk.
        covered: set[int] = set()
        for first, last in origin.range_requests:
            covered.update(range(first // (256 * 1024), last // (256 * 1024) + 1))
        assert set(range(start // (256 * 1024))) <= covered, "the backfill did not reach byte 0"
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_seeking_back_over_fetched_bytes_asks_the_host_for_nothing() -> None:
    """mpv answers a seek by opening a new connection to the same URL. What
    the previous one already pulled is still here, so going back and forward
    again costs no network at all."""
    origin = Origin()
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        status, _, body = await fetch(url)
        assert status == 200 and body == BODY
        origin.range_requests.clear()

        # Back to the beginning, then forward again: the same token, the same
        # cache, and a host that is never contacted.
        _, _, back = await fetch(url, span="bytes=0-")
        assert back == BODY
        _, _, forward = await fetch(url, span="bytes=1048576-")
        assert forward == BODY[1048576:]
        assert origin.range_requests == []
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_new_playback_does_not_inherit_the_previous_cache() -> None:
    """A registration is one playback. Keeping its bytes would keep a debrid
    URL's content around for the rest of the session."""
    origin = Origin()
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        await fetch(accelerator.local_url(origin.url))
        origin.range_requests.clear()
        await fetch(accelerator.local_url(origin.url))
        assert origin.range_requests, "the second playback served stale bytes"
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_the_cache_is_bounded_and_keeps_what_is_under_the_playhead() -> None:
    origin = Origin()
    await origin.start()
    # Room for four chunks of a twelve-chunk file.
    accelerator = SegmentedStreamProxy(
        connections=2, chunk_bytes=256 * 1024, cache_bytes=1024 * 1024
    )
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        status, _, body = await fetch(url)
        assert status == 200 and body == BODY
        cache = next(iter(accelerator._entries.values())).cache
        assert cache.total_bytes <= 1024 * 1024
        # Eviction is by distance from the playhead, and the playhead ended at
        # the last chunk, so that is what survived.
        assert (len(BODY) - 1) // (256 * 1024) in cache
    finally:
        await accelerator.aclose()
        await origin.aclose()


def test_chunk_cache_evicts_furthest_from_the_playhead() -> None:
    from gravitas.infrastructure.network.segmented_proxy import _ChunkCache

    cache = _ChunkCache(3 * 10)
    cache.anchor = 5
    for index in (5, 6, 4, 20):
        cache.put(index, b"x" * 10)
    assert 20 not in cache  # furthest from the playhead went first
    assert {4, 5, 6} <= set(range(30)) and 5 in cache and 6 in cache and 4 in cache


def test_chunk_cache_keeps_more_ahead_than_behind() -> None:
    """Ahead is what the proxy reads into, behind is what was played. Each side
    is judged against its own share, so with twice the room ahead a chunk
    three ahead outlives one two behind."""
    from gravitas.infrastructure.network.segmented_proxy import _ChunkCache

    cache = _ChunkCache(4 * 10, behind=10)
    cache.anchor = 10
    for index in (10, 8, 11, 12, 13):
        cache.put(index, b"x" * 10)
    assert 8 not in cache
    assert {10, 11, 12, 13} == {i for i in range(20) if i in cache}


def test_chunk_cache_on_disk_round_trips_and_cleans_up(tmp_path: Path) -> None:
    from gravitas.infrastructure.network.segmented_proxy import _ChunkCache

    directory = tmp_path / "stream"
    cache = _ChunkCache(2 * 10, directory=directory)
    cache.put(0, b"a" * 10)
    cache.put(1, b"b" * 10)
    assert cache.get(1) == b"b" * 10
    assert sorted(p.name for p in directory.iterdir()) == ["0.chunk", "1.chunk"]
    cache.put(5, b"c" * 10)  # over the limit: the furthest from the anchor goes
    assert 5 not in cache and not (directory / "5.chunk").exists()
    assert cache.total_bytes == 20

    # A file that vanished is a miss, not an error.
    (directory / "0.chunk").unlink()
    assert cache.get(0) is None and 0 not in cache

    cache.close()
    assert not directory.exists()
    cache.put(7, b"d" * 10)  # a straggler after close lands nowhere
    assert not directory.exists() and len(cache) == 0


async def test_a_disk_backed_proxy_serves_seeks_from_disk_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    """The disk cache is what makes a track switch's refresh seek local. Its
    bytes are a debrid stream's, so they go with the playback, and a crashed
    session's leftovers go at the next start."""
    root = tmp_path / "stream-proxy"
    root.mkdir()
    (root / "stale").mkdir()
    (root / "stale" / "0.chunk").write_bytes(b"old")

    origin = Origin()
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024, cache_dir=root)
    assert not accelerator.holds_stream_cache()
    await accelerator.start()
    try:
        assert accelerator.holds_stream_cache()
        assert not (root / "stale").exists()

        url = accelerator.local_url(origin.url)
        status, _, body = await fetch(url)
        assert status == 200 and body == BODY
        (stream_dir,) = list(root.iterdir())
        assert len(list(stream_dir.iterdir())) == -(-len(BODY) // (256 * 1024))

        origin.range_requests.clear()
        _, _, again = await fetch(url, span="bytes=524288-")
        assert again == BODY[524288:]
        assert origin.range_requests == []

        # The next playback retires this one's files.
        accelerator.local_url(origin.url)
        for _ in range(100):
            if not stream_dir.exists():
                break
            await asyncio.sleep(0.01)
        assert not stream_dir.exists()
    finally:
        await accelerator.aclose()
        await origin.aclose()
    assert not root.exists()


async def test_a_cache_dir_that_cannot_be_made_falls_back_to_memory(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_bytes(b"")
    origin = Origin()
    await origin.start()
    accelerator = SegmentedStreamProxy(
        connections=2, chunk_bytes=256 * 1024, cache_dir=blocker / "stream-proxy"
    )
    await accelerator.start()
    try:
        assert not accelerator.holds_stream_cache()
        status, _, body = await fetch(accelerator.local_url(origin.url))
        assert status == 200 and body == BODY
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_probe_that_could_not_reach_the_host_is_not_remembered() -> None:
    """A refused probe is a fact about one moment, not about the host.

    Recording it as "this host has no ranges" turned one bad request into a
    stream that could never be split again: every later connection went
    straight to the passthrough that had just failed, and mpv reported an
    instant end of file each time.
    """
    origin = Origin(refuse=2)  # the probe, then the passthrough
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(fetch(url), timeout=10)
        entry = next(iter(accelerator._entries.values()))
        assert entry.ranged is None, "a failed probe was cached as a verdict"

        # The next connection probes again and serves the stream properly.
        status, _, body = await asyncio.wait_for(fetch(url), timeout=20)
        assert status == 200
        assert body == BODY
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_stream_the_proxy_keeps_failing_is_handed_back_undecorated() -> None:
    """ "No worse than mpv on its own" has to hold when this module is the
    thing that is broken. After enough failures the proxy stands aside and
    the next play() or reconnect gets the upstream URL itself."""
    origin = Origin(refuse=99)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        for _ in range(3):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(fetch(url), timeout=10)
        assert accelerator.local_url(origin.url) == origin.url
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_quitting_mid_stream_leaves_no_pending_tasks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The app quits while a handler is serving. Its workers have to go with
    it.

    Cancelling the handler interrupts the awaits in its own teardown, so the
    cancellation of the workers has to survive that -- otherwise the loop
    closes on seven live tasks and prints "Task was destroyed but it is
    pending" once each, at the exact moment the app is quitting.
    """
    origin = Origin(delay_s=0.2)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=6, chunk_bytes=256 * 1024)
    await accelerator.start()
    reading = asyncio.create_task(fetch(accelerator.local_url(origin.url)))
    await asyncio.sleep(0.3)  # mid-stream, workers in flight
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        await accelerator.aclose()
        reading.cancel()
        with contextlib.suppress(Exception):
            await reading
        await origin.aclose()
        gc.collect()
        await asyncio.sleep(0)
    assert [r for r in caplog.records if "was destroyed" in r.getMessage()] == []


async def test_a_rate_limited_host_gets_fewer_readers_not_a_dead_playback() -> None:
    """429 is the host asking for fewer connections, not a failed stream.

    Treated as a failed block it spent the retry budget, ended mpv's
    connection mid-episode ("Stream ends prematurely") and dropped the viewer
    into stall recovery. The answer is to stand workers down and carry on: the
    file still arrives, byte for byte.
    """
    # A little latency, so six workers really are in flight at once. A small
    # body keeps the convergence (and this test) to a couple of seconds.
    # Big enough that six runs of chunks are in flight at once: a small
    # body is one run, one request, and never meets the limit at all.
    small = BODY
    origin = Origin(concurrency=2, delay_s=0.05, body=small)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=6, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        status, _, body = await asyncio.wait_for(
            fetch(accelerator.local_url(origin.url)), timeout=60
        )
        assert status == 200
        assert body == small
        assert origin.rate_limited > 0  # the limit really was hit
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_the_shrunk_connection_count_outlives_the_connection() -> None:
    """A seek opens a new connection to the same stream. Starting it back at
    six readers would re-earn the 429 every time."""
    # Big enough that six runs of chunks are in flight at once: a small
    # body is one run, one request, and never meets the limit at all.
    small = BODY
    origin = Origin(concurrency=1, delay_s=0.05, body=small)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=6, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        await asyncio.wait_for(fetch(url), timeout=60)
        entry = next(iter(accelerator._entries.values()))
        assert entry.parallel < 6
        settled = entry.parallel
        origin.rate_limited = 0
        # A seek: same stream, new connection, and it must not go back up.
        status, _, body = await asyncio.wait_for(fetch(url, span="bytes=400000-"), timeout=60)
        assert status == 206
        assert body == small[400000:]
        assert entry.parallel <= settled
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_the_next_stream_from_a_host_starts_at_the_limit_it_learned() -> None:
    """A new stream is a new URL (a new debrid token), and starting it back at
    the full count re-earned the same 429s every playback -- measured on a
    TorBox CDN as a start-up stalled while six readers shrank to one."""
    # Big enough that six runs of chunks are in flight at once: a small
    # body is one run, one request, and never meets the limit at all.
    small = BODY
    origin = Origin(concurrency=1, delay_s=0.05, body=small)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=6, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        await asyncio.wait_for(fetch(accelerator.local_url(origin.url)), timeout=60)
        learned = next(iter(accelerator._entries.values())).parallel
        assert learned < 6

        # Same host, different stream: it starts where the host left off.
        accelerator.local_url(origin.url + "?next-episode")
        assert next(iter(accelerator._entries.values())).parallel == learned

        # And the lesson is forgotten once it is old: limits can lift.
        with accelerator._entries_lock:
            for host, (limit, _) in list(accelerator._host_limits.items()):
                accelerator._host_limits[host] = (limit, -1e9)
        accelerator.local_url(origin.url + "?much-later")
        assert next(iter(accelerator._entries.values())).parallel == 6
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_host_that_refuses_the_connection_fails_the_load() -> None:
    """A redirect is advice, and advice to dial a dead host is worth nothing.

    The proxy has just proved from this machine that the connection is
    refused, so ffmpeg would only rediscover that a retry ladder later -- once
    per source, while the viewer watches a spinner. A 502 ends the load now,
    which is what lets the player move to the next source.
    """
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        # Port 1 on loopback: nothing listens there, and nothing will.
        dead = "http://127.0.0.1:1/gone.mkv"
        status, _, _ = await asyncio.wait_for(fetch(accelerator.local_url(dead)), timeout=20)
        assert status == 502
    finally:
        await accelerator.aclose()


async def test_the_refused_host_is_remembered_for_the_player_to_show() -> None:
    """The player can only say "this source didn't respond", which reads like
    a broken app. The host that refused exists only in here."""
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        assert accelerator.last_failure() is None
        dead = "http://127.0.0.1:1/gone.mkv"
        await asyncio.wait_for(fetch(accelerator.local_url(dead)), timeout=20)
        reason = accelerator.last_failure()
        assert reason is not None
        assert "127.0.0.1" in reason
        # And a fresh stream does not inherit it.
        accelerator.local_url("http://127.0.0.2:1/other.mkv")
        assert accelerator.last_failure() is None
    finally:
        await accelerator.aclose()


async def test_a_sinkholed_host_is_named_as_a_dns_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocklist answers 0.0.0.0, and the connection that follows is refused
    instantly -- identical to a dead node from the socket, and a completely
    different thing to do about it."""

    async def blocked(host: str, port: object, **kwargs: object) -> list[object]:
        return [(2, 1, 6, "", ("0.0.0.0", 0))]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", blocked)
    assert "DNS" in await _describe_unreachable("blocked.example")


async def test_a_host_that_does_not_resolve_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    async def missing(host: str, port: object, **kwargs: object) -> list[object]:
        raise OSError("nope")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", missing)
    assert "does not resolve" in await _describe_unreachable("gone.example")


async def test_a_host_that_answers_and_then_dies_is_redirected_not_dropped() -> None:
    """The proxy failing must not be the same thing as playback failing.

    Closing the socket left mpv reconnecting to a local URL that could never
    work -- its own ffmpeg retry loop never gives up, so the spinner never
    ends. The host is reachable here, so it may well serve mpv better than it
    served this module: the request is handed to the real URL, and ffmpeg
    follows a 302 by itself.
    """
    origin = Origin(refuse=99)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        status, headers, _ = await asyncio.wait_for(
            fetch(accelerator.local_url(origin.url)), timeout=20
        )
        assert status == 302
        assert headers["location"] == origin.url
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_retired_stream_is_redirected_without_another_attempt() -> None:
    origin = Origin(refuse=99)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=2, chunk_bytes=256 * 1024)
    await accelerator.start()
    try:
        url = accelerator.local_url(origin.url)
        for _ in range(_MAX_ENTRY_FAILURES):
            status, _, _ = await asyncio.wait_for(fetch(url), timeout=20)
            assert status == 302  # recovery happens on the first failure
        origin.headers_seen.clear()
        again, headers, _ = await asyncio.wait_for(fetch(url), timeout=20)
        assert again == 302
        assert headers["location"] == origin.url
        # And the host was not contacted a second time to learn that.
        assert origin.headers_seen == []
        # The next play() gets the URL itself, so mpv never reaches the proxy.
        assert accelerator.local_url(origin.url) == origin.url
    finally:
        await accelerator.aclose()
        await origin.aclose()


async def test_a_rate_limited_probe_is_waited_out_not_taken_as_no_ranges() -> None:
    """The probe's answer is a 429, not a 206 -- and it used to be recorded as
    "this host has no ranges", cached for the stream. Every connection then
    went through the passthrough, which handed mpv the 429 itself: the source
    never opened, and the reconnect that finally did could not seek to the
    file's index and landed at the end of the episode. A limit at the probe is
    waited out like a limit on a block."""
    origin = Origin(limit_first=2)
    await origin.start()
    accelerator = SegmentedStreamProxy(connections=4, chunk_bytes=64 * 1024)
    await accelerator.start()
    try:
        status, _, body = await asyncio.wait_for(
            fetch(accelerator.local_url(origin.url)), timeout=30
        )
        assert status == 200
        assert body == BODY
        assert origin.rate_limited == 2
        # Split into ranged requests after all: the probe learned the truth.
        assert len(origin.range_requests) > 1
    finally:
        await accelerator.aclose()
        await origin.aclose()
