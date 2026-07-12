"""Use case: fetch one page of a single catalog for the Discover board."""

from __future__ import annotations

from dataclasses import dataclass

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaItem, MediaType


@dataclass(frozen=True, slots=True)
class BoardPage:
    items: list[MediaItem]
    has_more: bool


class BrowseBoard:
    PAGE_SIZE = 100

    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(
        self,
        addon_id: str,
        type: MediaType,
        catalog_id: str,
        *,
        genre: str | None = None,
        skip: int = 0,
    ) -> BoardPage:
        resolved = self._repo.resolve_catalog(addon_id, type, catalog_id)
        if resolved is None:
            return BoardPage(items=[], has_more=False)
        manifest, ref = resolved
        items = await self._repo.fetch_catalog_page(manifest, ref, genre=genre, skip=skip)
        return BoardPage(items=items, has_more=len(items) == self.PAGE_SIZE)
