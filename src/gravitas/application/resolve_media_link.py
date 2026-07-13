"""Use case: resolve a pasted IMDB/TVDB id to a previewable MediaItem."""

from __future__ import annotations

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.errors import AddonUnreachable, GravitasError
from gravitas.domain.models import MediaItem, MediaType
from gravitas.domain.ports import ExternalIdResolver


class ResolveMediaLink:
    def __init__(self, repo: AddonRepository, resolver: ExternalIdResolver) -> None:
        self._repo = repo
        self._resolver = resolver

    async def __call__(self, source: str, external_id: str) -> MediaItem:
        if source == "imdb":
            # IMDB ids are native to the addons; try movie then series.
            types: tuple[MediaType, ...] = ("movie", "series")
            for media_type in types:
                try:
                    meta = await self._repo.meta(media_type, external_id)
                except GravitasError:
                    continue
                return MediaItem(
                    id=meta.id, type=meta.type, name=meta.name, poster=meta.poster, year=meta.year
                )
            raise AddonUnreachable(f"no metadata found for {external_id}")
        # TVDB (and any non-native source): resolve via the external resolver.
        resolved = await self._resolver.resolve(source, external_id)
        return MediaItem(
            id=resolved.imdb_id,
            type=resolved.type,
            name=resolved.name,
            poster=resolved.poster,
            year=resolved.year,
        )
