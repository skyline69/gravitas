"""Content cache for posters/artwork keyed by URL hash."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from gravitas.domain.errors import AddonUnreachable


class DiskCache:
    def __init__(self, client: httpx.AsyncClient, root: Path) -> None:
        self._client = client
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._root / digest

    async def get_or_fetch(self, url: str) -> bytes:
        path = self._path_for(url)
        if path.exists():
            return path.read_bytes()
        try:
            response = await self._client.get(url, follow_redirects=True, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AddonUnreachable(f"GET {url} failed: {exc}") from exc
        data = response.content
        path.write_bytes(data)
        return data
