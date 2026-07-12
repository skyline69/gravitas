"""In-memory store of installed addons plus catalog aggregation helpers."""

from __future__ import annotations

import logging

from gravitas.domain.errors import GravitasError
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)
from gravitas.domain.ports import AddonSource

_log = logging.getLogger(__name__)


class AddonRepository:
    def __init__(self, source: AddonSource) -> None:
        self._source = source
        self._manifests: list[AddonManifest] = []

    async def install(self, url: str) -> AddonManifest:
        manifest = await self._source.fetch_manifest(url)
        self._manifests = [m for m in self._manifests if m.id != manifest.id]
        self._manifests.append(manifest)
        return manifest

    def installed(self) -> list[AddonManifest]:
        return list(self._manifests)

    def catalog_refs(self) -> list[tuple[AddonManifest, CatalogRef]]:
        return [(m, ref) for m in self._manifests for ref in m.catalogs]

    async def aggregate_catalog(self, ref_owner: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        try:
            return await self._source.fetch_catalog(ref_owner, ref)
        except GravitasError as exc:
            _log.warning("catalog fetch failed for %s/%s: %s", ref_owner.id, ref.id, exc)
            return []

    async def meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        return await self._source.fetch_meta(manifest, type, id)

    async def streams(self, manifest: AddonManifest, type: MediaType, id: str) -> list[Stream]:
        return await self._source.fetch_streams(manifest, type, id)
