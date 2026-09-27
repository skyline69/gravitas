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
    Subtitle,
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


@dataclass(frozen=True, slots=True)
class StreamFetch:
    """Every installed addon's answer for one video, merged.

    `complete` is False when an addon failed to answer: the list is still the
    best there is to show, but it is not an answer worth remembering.
    """

    streams: tuple[Stream, ...]
    complete: bool


def _merge_streams(results: list[list[Stream] | None]) -> tuple[Stream, ...]:
    """Concatenate per-addon answers in install order, dropping a link an
    earlier addon already listed. A None (an addon that failed) adds nothing."""
    collected: list[Stream] = []
    seen: set[str] = set()
    for fetched in results:
        for stream in fetched or ():
            key = stream.playable_url or stream.external_url or stream.info_hash
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            collected.append(stream)
    return tuple(collected)


class AddonRepository:
    def __init__(self, source: AddonSource) -> None:
        self._source = source
        self._manifests: list[AddonManifest] = []
        self._protected: set[str] = set()
        # id -> manifest URL it was installed from, for persistence.
        self._urls: dict[str, str] = {}

    async def install(self, url: str, *, protected: bool = False) -> AddonManifest:
        manifest = await self._source.fetch_manifest(url)
        previous = next((m for m in self._manifests if m.id == manifest.id), None)
        self._manifests = [m for m in self._manifests if m.id != manifest.id]
        self._manifests.append(manifest)
        self._urls[manifest.id] = url
        if protected:
            self._protected.add(manifest.id)
        # Said once per manifest, not once per install: the boot installs every
        # addon twice (cached, then revalidated), and a line repeated on every
        # launch is one nobody reads -- including the day it changes.
        unchanged = previous == manifest
        (_log.debug if unchanged else _log.info)(
            "%s addon %s v%s (%d catalogs%s)",
            "reinstalled unchanged" if unchanged else "installed",
            manifest.id,
            manifest.version,
            len(manifest.catalogs),
            ", protected" if protected else "",
        )
        if not unchanged:
            for ignored in manifest.ignored_catalogs:
                kind, _, catalog_id = ignored.partition(":")
                _log.info(
                    "addon %s: ignoring %r catalog %r (only movie/series are supported)",
                    manifest.id,
                    kind,
                    catalog_id,
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

    async def stored_meta(self, type: MediaType, id: str) -> MetaDetail | None:
        """The first meta addon's answer for this title kept on disk, of any
        age -- the same order meta() asks in. A placeholder for meta(), which
        is still asked."""
        for manifest in self._manifests:
            if not manifest.serves("meta", type, id):
                continue
            try:
                stored = await self._source.stored_meta(manifest, type, id)
            except GravitasError as exc:
                _log.warning("stored meta unreadable for %s: %s", manifest.id, exc)
                continue
            if stored is not None:
                return stored
        return None

    async def streams(self, type: MediaType, id: str) -> list[Stream]:
        return list((await self.fetch_streams(type, id)).streams)

    async def fetch_streams(self, type: MediaType, id: str) -> StreamFetch:
        # Aggregate across every stream-capable addon, fault-isolated: one
        # failing addon is logged and skipped, never blocking the others.
        # Asked CONCURRENTLY: a stream addon routinely takes seconds to answer,
        # and asking them in turn made the page wait for the sum of them. The
        # merge still runs in install order, so which duplicate wins is the
        # same as it always was.
        targets = [m for m in self._manifests if m.serves("stream", type, id)]
        results = await asyncio.gather(*(self._streams_one(m, type, id) for m in targets))
        return StreamFetch(_merge_streams(results), complete=all(r is not None for r in results))

    async def subtitles(self, type: MediaType, id: str) -> list[Subtitle]:
        """Every subtitle file the installed addons offer for this video,
        asked concurrently and fault-isolated like streams. The same file
        offered twice (by url) is listed once."""
        targets = [m for m in self._manifests if m.serves("subtitles", type, id)]
        results = await asyncio.gather(*(self._subtitles_one(m, type, id) for m in targets))
        seen: set[str] = set()
        merged: list[Subtitle] = []
        for found in results:
            for subtitle in found:
                if subtitle.url in seen:
                    continue
                seen.add(subtitle.url)
                merged.append(subtitle)
        return merged

    async def _subtitles_one(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Subtitle]:
        try:
            return await self._source.fetch_subtitles(manifest, type, id)
        except GravitasError as exc:
            _log.warning("subtitles fetch failed for %s: %s", manifest.id, exc)
            return []

    async def stored_streams(self, type: MediaType, id: str) -> list[Stream] | None:
        """What the stream addons answered for this video last time, merged
        the same way, or None if none of them has an answer on disk. Links in
        it may have expired: it is a placeholder for fetch_streams, which is
        still asked, never a replacement."""
        targets = [m for m in self._manifests if m.serves("stream", type, id)]
        results = await asyncio.gather(*(self._stored_one(m, type, id) for m in targets))
        if all(r is None for r in results):
            return None
        return list(_merge_streams(results))

    async def _stored_one(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream] | None:
        try:
            return await self._source.stored_streams(manifest, type, id)
        except GravitasError as exc:
            _log.warning("stored streams unreadable for %s: %s", manifest.id, exc)
            return None

    async def _streams_one(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream] | None:
        try:
            fetched = await self._source.fetch_streams(manifest, type, id)
        except GravitasError as exc:
            _log.warning("stream fetch failed for %s: %s", manifest.id, exc)
            return None
        _log.debug("%s returned %d streams for %s", manifest.id, len(fetched), id)
        return fetched

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
