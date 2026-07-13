"""Use case: fetch streams and keep only directly-playable URLs (MVP: no torrents)."""

from __future__ import annotations

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.errors import NoStreams
from gravitas.domain.models import MediaType, Stream


class ResolveStream:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, type: MediaType, id: str) -> list[Stream]:
        streams = await self._repo.streams(type, id)
        direct = [s for s in streams if s.is_direct]
        if not direct:
            raise NoStreams(f"no direct-URL streams for {id}")
        return direct
