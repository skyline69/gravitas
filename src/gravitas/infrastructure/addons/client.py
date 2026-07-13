"""AddonSource implementation backed by an httpx.AsyncClient."""

from __future__ import annotations

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

_MANIFEST_SUFFIX = "manifest.json"


class AddonClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get_json(self, url: str) -> dict[str, Any]:
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
        return data

    async def fetch_manifest(self, url: str) -> AddonManifest:
        data = await self._get_json(url)
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
        data = await self._get_json(manifest.base_url + path)
        return parsing.parse_catalog(data)

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        data = await self._get_json(manifest.base_url + parsing.meta_path(type, id))
        return parsing.parse_meta(data)

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        data = await self._get_json(manifest.base_url + parsing.stream_path(type, id))
        return parsing.parse_streams(data)
