"""AddonSource implementation backed by an httpx.AsyncClient.

CPU work (JSON decode, response parsing) runs in a worker thread via
asyncio.to_thread: the app's asyncio loop IS the Qt GUI thread (qasync), so
milliseconds spent decoding a fat Cinemeta catalog on the loop are
milliseconds the render loop cannot sync — visible as animation hitches
wherever a spinner or transition is running while fetches land.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from gravitas.domain.errors import AddonUnreachable, InvalidResponse
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)
from gravitas.infrastructure.addons import parsing
from gravitas.infrastructure.cache.json_disk_cache import JsonDiskCache
from gravitas.infrastructure.cache.ttl_cache import TtlCache
from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)

_MANIFEST_SUFFIX = "manifest.json"

# How long a DISK entry may satisfy a normal (non-stale) read. In-memory the
# manifest is cached for the session (cache_for=inf) because reinstalling is
# what refreshes it — but "for the session" must not become "forever" once
# entries persist, or an upgraded addon would never be seen again. A day
# bounds every kind without touching the in-memory policy.
_DISK_FRESH_CAP = 24 * 60 * 60.0


class AddonClient:
    # How long each kind of response stays fresh. Everything here is a network
    # round-trip to somebody else's server, so the question is only how wrong a
    # cached answer can be.
    #
    # A manifest describes the addon itself and changes when it is upgraded —
    # reinstalling it is what a user does then, and that rebuilds the client's
    # world anyway, so the session is a safe lifetime.
    MANIFEST_TTL = float("inf")
    # Meta is the title, cast, artwork and episode list: it barely moves, and
    # reopening a title is the commonest navigation in the app.
    META_TTL = 24 * 60 * 60.0
    # A catalog is "what is popular now", so it has to stay live-ish.
    CATALOG_TTL = 15 * 60.0
    # Streams are deliberately absent: addon and debrid links are frequently
    # time-limited or single-use, and handing back a stale one fails playback
    # with no obvious cause. _get_json defaults to not caching, so a new
    # endpoint is uncached until someone decides otherwise.

    def __init__(
        self,
        client: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
        disk: JsonDiskCache | None = None,
    ) -> None:
        self._client = client
        self._cache: TtlCache[dict[str, Any]] = TtlCache(clock=clock)
        self._disk = disk
        # Boot's stale-while-revalidate switch (flipped by the composition
        # root): while True, a disk entry of ANY age satisfies a read, so a
        # warm launch paints instantly; the boot's second pass then runs with
        # it off and refreshes whatever was actually stale. Stale hits are
        # never promoted to the in-memory cache — that would make the second
        # pass read the stale data back as "fresh".
        self.serve_stale = False

    async def _get_json(self, url: str, *, cache_for: float = 0.0) -> dict[str, Any]:
        cached = self._cache.get(url) if cache_for > 0 else None
        if cached is not None:
            _log.debug("cache hit (memory): %s", url)
            return cached
        if self._disk is not None and cache_for > 0:
            hit = await asyncio.to_thread(self._disk.get, url)
            if hit is not None:
                data, age = hit
                if age <= min(cache_for, _DISK_FRESH_CAP):
                    # Fresh enough: promote for the freshness it has left.
                    self._cache.put(url, data, ttl=cache_for - age)
                    _log.debug("cache hit (disk, %.0fs old): %s", age, url)
                    return data
                if self.serve_stale:
                    _log.debug("serving stale disk entry (%.0fs old): %s", age, url)
                    return data
        started = time.monotonic()
        try:
            response = await self._client.get(url, follow_redirects=True, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AddonUnreachable(f"GET {url} failed: {exc}") from exc
        _log.info(
            "GET %s -> %d (%.0f ms, %.1f kB)",
            abbreviate_url(url),
            response.status_code,
            (time.monotonic() - started) * 1000,
            len(response.content) / 1024,
        )
        try:
            data = await asyncio.to_thread(response.json)
        except ValueError as exc:
            raise InvalidResponse(f"non-JSON response from {url}") from exc
        if not isinstance(data, dict):
            raise InvalidResponse(f"expected JSON object from {url}")
        # Only a good response is cached: a dead addon must not poison the
        # cache for the next quarter of an hour.
        self._cache.put(url, data, ttl=cache_for)
        if self._disk is not None and cache_for > 0:
            await asyncio.to_thread(self._disk.put, url, data)
        return data

    async def fetch_manifest(self, url: str) -> AddonManifest:
        data = await self._get_json(url, cache_for=AddonClient.MANIFEST_TTL)
        base_url = url[: -len(_MANIFEST_SUFFIX)] if url.endswith(_MANIFEST_SUFFIX) else url
        return parsing.parse_manifest(data, base_url=base_url)

    async def fetch_catalog(
        self,
        manifest: AddonManifest,
        ref: CatalogRef,
        *,
        genre: str | None = None,
        skip: int = 0,
        search: str | None = None,
    ) -> list[MediaItem]:
        # Always through catalog_path_extra: it falls back to the bare path when
        # there is nothing to encode, and it is what supplies the default genre
        # a genre-required catalog must be asked with.
        path = parsing.catalog_path_extra(ref, genre, skip, search)
        data = await self._get_json(manifest.base_url + path, cache_for=AddonClient.CATALOG_TTL)
        # Catalogs and meta are the two big payloads (hundreds of items /
        # full episode lists); their parse loops leave the GUI thread too.
        return await asyncio.to_thread(parsing.parse_catalog, data)

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        data = await self._get_json(
            manifest.base_url + parsing.meta_path(type, id), cache_for=AddonClient.META_TTL
        )
        return await asyncio.to_thread(parsing.parse_meta, data)

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        data = await self._get_json(manifest.base_url + parsing.stream_path(type, id))
        return parsing.parse_streams(data)
