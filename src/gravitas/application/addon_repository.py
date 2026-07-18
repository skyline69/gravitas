"""In-memory store of installed addons plus catalog aggregation helpers."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator
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
    # A genre-required catalog has no "all genres" reading: the addon will not
    # answer without one, so the picker must not offer that choice.
    requires_genre: bool = False


class AddonRepository:
    def __init__(self, source: AddonSource) -> None:
        self._source = source
        self._manifests: list[AddonManifest] = []
        self._protected: set[str] = set()
        # id -> manifest URL it was installed from, for persistence.
        self._urls: dict[str, str] = {}

    async def install(self, url: str, *, protected: bool = False) -> AddonManifest:
        manifest = await self._source.fetch_manifest(url)
        self._manifests = [m for m in self._manifests if m.id != manifest.id]
        self._manifests.append(manifest)
        self._urls[manifest.id] = url
        if protected:
            self._protected.add(manifest.id)
        _log.info(
            "installed addon %s v%s (%d catalogs%s)",
            manifest.id,
            manifest.version,
            len(manifest.catalogs),
            ", protected" if protected else "",
        )
        return manifest

    def installed(self) -> list[AddonManifest]:
        return list(self._manifests)

    def user_addon_urls(self) -> list[str]:
        """Manifest URLs of user-installed (non-protected) addons, in install
        order — the set worth persisting; bootstrap reinstalls protected ones."""
        return [
            self._urls[m.id]
            for m in self._manifests
            if m.id not in self._protected and m.id in self._urls
        ]

    def uninstall(self, addon_id: str) -> None:
        if addon_id in self._protected:
            raise AddonRemovalError(f"{addon_id} is protected and cannot be removed")
        remaining = [m for m in self._manifests if m.id != addon_id]
        if len(remaining) == len(self._manifests):
            raise AddonRemovalError(f"no installed addon with id {addon_id}")
        self._manifests = remaining
        self._urls.pop(addon_id, None)

    def is_protected(self, addon_id: str) -> bool:
        return addon_id in self._protected

    def catalog_refs(self) -> list[tuple[AddonManifest, CatalogRef]]:
        # Non-browsable catalogs are excluded, not merely left unfetched: they
        # demand extras only a Stremio client with a user library can supply
        # (Cinemeta's last-videos/calendar-videos). Asked bare, a lenient addon
        # answers with arbitrary items, which is how they reached Home as junk.
        return [(m, ref) for m in self._manifests for ref in m.catalogs if ref.is_browsable]

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
        # Query every searchable catalog of every installed addon CONCURRENTLY
        # (latency = the slowest single request, not the sum), fault-isolated
        # per catalog. Then merge in catalog order, dedup by id, cap the total.
        targets = [
            (manifest, ref)
            for manifest in self._manifests
            for ref in manifest.catalogs
            if ref.supports_search
        ]
        if not targets:
            return []
        results = await asyncio.gather(
            *(self._search_one(manifest, ref, query) for manifest, ref in targets)
        )
        collected: list[MediaItem] = []
        seen: set[str] = set()
        for items in results:
            for item in items:
                if item.id in seen:
                    continue
                seen.add(item.id)
                collected.append(item)
                if len(collected) >= 60:
                    return collected
        return collected

    async def _search_one(
        self, manifest: AddonManifest, ref: CatalogRef, query: str
    ) -> list[MediaItem]:
        try:
            return await self._source.fetch_catalog(manifest, ref, search=query)
        except GravitasError as exc:
            _log.warning("search failed for %s/%s: %s", manifest.id, ref.id, exc)
            return []

    async def search_stream(self, query: str) -> AsyncIterator[list[MediaItem]]:
        # Same as search(), but yields each catalog's fresh (deduped) items as
        # soon as that request completes — so the UI can show the fastest
        # addon's results without waiting for the slowest. Order is completion
        # order, not catalog order.
        targets = [
            (manifest, ref)
            for manifest in self._manifests
            for ref in manifest.catalogs
            if ref.supports_search
        ]
        if not targets:
            return
        seen: set[str] = set()
        total = 0
        for coro in asyncio.as_completed(
            [self._search_one(manifest, ref, query) for manifest, ref in targets]
        ):
            items = await coro
            fresh: list[MediaItem] = []
            for item in items:
                if item.id in seen:
                    continue
                seen.add(item.id)
                fresh.append(item)
                total += 1
                if total >= 60:
                    break
            if fresh:
                yield fresh
            if total >= 60:
                return

    async def meta(self, type: MediaType, id: str) -> MetaDetail:
        # Try each metadata-capable addon in install order; the first that
        # succeeds wins (e.g. an addon whose meta covers only "library" 404s
        # for a movie and we fall through to the next).
        last_error: GravitasError | None = None
        for manifest in self._manifests:
            # serves() also honours the addon's declared types and idPrefixes:
            # asking Cinemeta (idPrefixes ["tt"]) for a kitsu: id is a
            # guaranteed 404, and its failure would mask the real last_error.
            if not manifest.serves("meta", type, id):
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
            if not manifest.serves("stream", type, id):
                continue
            try:
                fetched = await self._source.fetch_streams(manifest, type, id)
            except GravitasError as exc:
                _log.warning("stream fetch failed for %s: %s", manifest.id, exc)
                continue
            _log.debug("%s returned %d streams for %s", manifest.id, len(fetched), id)
            for stream in fetched:
                key = stream.playable_url or stream.external_url or stream.info_hash
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
        # Same browsable filter as catalog_refs: Discover must not offer a
        # board the addon will not serve without extras we cannot supply.
        browsable = [(m, ref) for m in self._manifests for ref in m.catalogs if ref.is_browsable]
        name_counts = Counter(ref.name for _, ref in browsable)
        options: list[CatalogOption] = []
        for manifest, ref in browsable:
            label = ref.name if name_counts[ref.name] == 1 else f"{ref.name} ({manifest.name})"
            options.append(
                CatalogOption(
                    addon_id=manifest.id,
                    type=ref.type,
                    catalog_id=ref.id,
                    label=label,
                    genres=ref.genres,
                    requires_genre=ref.requires_genre,
                )
            )
        return options
