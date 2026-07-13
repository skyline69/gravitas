"""Use case: search movies/series across installed addons."""

from __future__ import annotations

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaItem


class SearchMedia:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, query: str) -> list[MediaItem]:
        query = query.strip()
        if not query:
            return []
        return await self._repo.search(query)
