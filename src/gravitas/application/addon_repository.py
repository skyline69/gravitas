"""In-memory store of installed addons plus catalog aggregation helpers."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from gravitas.domain.errors import AddonRemovalError, AddonUnreachable, GravitasError
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


@dataclass(frozen=True, slots=True)
class CatalogOption:
    addon_id: str
    type: MediaType
    catalog_id: str
    label: str
    genres: tuple[str, ...]


class AddonRepository:
    def __init__(self, source: AddonSource) -> None:
        self._source = source
        self._manifests: list[AddonManifest] = []
        self._protected: set[str] = set()

    async def install(self, url: str, *, protected: bool = False) -> AddonManifest:
        manifest = await self._source.fetch_manifest(url)
        self._manifests = [m for m in self._manifests if m.id != manifest.id]
        self._manifests.append(manifest)
        if protected:
            self._protected.add(manifest.id)
        return manifest

    def installed(self) -> list[AddonManifest]:
        return list(self._manifests)

    def uninstall(self, addon_id: str) -> None:
        if addon_id in self._protected:
            raise AddonRemovalError(f"{addon_id} is protected and cannot be removed")
        remaining = [m for m in self._manifests if m.id != addon_id]
        if len(remaining) == len(self._manifests):
            raise AddonRemovalError(f"no installed addon with id {addon_id}")
        self._manifests = remaining

    def is_protected(self, addon_id: str) -> bool:
        return addon_id in self._protected

    def catalog_refs(self) -> list[tuple[AddonManifest, CatalogRef]]:
        return [(m, ref) for m in self._manifests for ref in m.catalogs]

    async def aggregate_catalog(self, ref_owner: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        try:
            return await self._source.fetch_catalog(ref_owner, ref)
        except GravitasError as exc:
            _log.warning("catalog fetch failed for %s/%s: %s", ref_owner.id, ref.id, exc)
            return []

    async def fetch_catalog_page(
        self, manifest: AddonManifest, ref: CatalogRef, *, genre: str | None = None, skip: int = 0
    ) -> list[MediaItem]:
        return await self._source.fetch_catalog(manifest, ref, genre=genre, skip=skip)

    async def search(self, query: str) -> list[MediaItem]:
        # Aggregate across every searchable catalog of every installed addon,
        # fault-isolated per addon; dedup by id (keep first); cap the total.
        collected: list[MediaItem] = []
        seen: set[str] = set()
        for manifest in self._manifests:
            for ref in manifest.catalogs:
                if not ref.supports_search:
                    continue
                try:
                    items = await self._source.fetch_catalog(manifest, ref, search=query)
                except GravitasError as exc:
                    _log.warning("search failed for %s/%s: %s", manifest.id, ref.id, exc)
                    continue
                for item in items:
                    if item.id in seen:
                        continue
                    seen.add(item.id)
                    collected.append(item)
                    if len(collected) >= 60:
                        return collected
        return collected

    async def meta(self, type: MediaType, id: str) -> MetaDetail:
        # Try each metadata-capable addon in install order; the first that
        # succeeds wins (e.g. an addon whose meta covers only "library" 404s
        # for a movie and we fall through to the next).
        last_error: GravitasError | None = None
        for manifest in self._manifests:
            if "meta" not in manifest.resources:
                continue
            try:
                return await self._source.fetch_meta(manifest, type, id)
            except GravitasError as exc:
                _log.warning("meta fetch failed for %s: %s", manifest.id, exc)
                last_error = exc
        if last_error is not None:
            raise last_error
        raise AddonUnreachable(f"no installed addon provides metadata for {id}")

    async def streams(self, type: MediaType, id: str) -> list[Stream]:
        # Aggregate across every stream-capable addon, fault-isolated: one
        # failing addon is logged and skipped, never blocking the others.
        collected: list[Stream] = []
        seen: set[str] = set()
        for manifest in self._manifests:
            if "stream" not in manifest.resources:
                continue
            try:
                fetched = await self._source.fetch_streams(manifest, type, id)
            except GravitasError as exc:
                _log.warning("stream fetch failed for %s: %s", manifest.id, exc)
                continue
            for stream in fetched:
                key = stream.url or stream.info_hash
                if key is not None:
                    if key in seen:
                        continue
                    seen.add(key)
                collected.append(stream)
        return collected

    def resolve_catalog(
        self, addon_id: str, type: MediaType, catalog_id: str
    ) -> tuple[AddonManifest, CatalogRef] | None:
        for manifest in self._manifests:
            if manifest.id != addon_id:
                continue
            for ref in manifest.catalogs:
                if ref.type == type and ref.id == catalog_id:
                    return manifest, ref
        return None

    def catalog_options(self) -> list[CatalogOption]:
        name_counts = Counter(ref.name for m in self._manifests for ref in m.catalogs)
        options: list[CatalogOption] = []
        for manifest in self._manifests:
            for ref in manifest.catalogs:
                label = ref.name if name_counts[ref.name] == 1 else f"{ref.name} ({manifest.name})"
                options.append(
                    CatalogOption(
                        addon_id=manifest.id,
                        type=ref.type,
                        catalog_id=ref.id,
                        label=label,
                        genres=ref.genres,
                    )
                )
        return options
