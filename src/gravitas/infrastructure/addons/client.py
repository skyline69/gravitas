"""AddonSource implementation backed by an httpx.AsyncClient."""

from __future__ import annotations

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
from gravitas.infrastructure.cache.ttl_cache import TtlCache

_MANIFEST_SUFFIX = "manifest.json"


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
    ) -> None:
        self._client = client
        self._cache: TtlCache[dict[str, Any]] = TtlCache(clock=clock)

    async def _get_json(self, url: str, *, cache_for: float = 0.0) -> dict[str, Any]:
        cached = self._cache.get(url) if cache_for > 0 else None
        if cached is not None:
            return cached
        try:
            response = await self._client.get(url, follow_redirects=True, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AddonUnreachable(f"GET {url} failed: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise InvalidResponse(f"non-JSON response from {url}") from exc
        if not isinstance(data, dict):
            raise InvalidResponse(f"expected JSON object from {url}")
        # Only a good response is cached: a dead addon must not poison the
        # cache for the next quarter of an hour.
        self._cache.put(url, data, ttl=cache_for)
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
        path = (
            parsing.catalog_path_extra(ref, genre, skip, search)
            if (genre or skip or search)
            else parsing.catalog_path(ref)
        )
        data = await self._get_json(manifest.base_url + path, cache_for=AddonClient.CATALOG_TTL)
        return parsing.parse_catalog(data)

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        data = await self._get_json(
            manifest.base_url + parsing.meta_path(type, id), cache_for=AddonClient.META_TTL
        )
        return parsing.parse_meta(data)

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        data = await self._get_json(manifest.base_url + parsing.stream_path(type, id))
        return parsing.parse_streams(data)
