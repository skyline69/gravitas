"""Use case: fetch streams and keep only directly-playable URLs (MVP: no torrents)."""

from __future__ import annotations

from gravitas.domain.errors import NoStreams
from gravitas.domain.models import AddonManifest, MediaType, Stream
from gravitas.infrastructure.addons.repository import AddonRepository


class ResolveStream:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, manifest: AddonManifest, type: MediaType, id: str) -> list[Stream]:
        streams = await self._repo.streams(manifest, type, id)
        direct = [s for s in streams if s.is_direct]
        if not direct:
            raise NoStreams(f"no direct-URL streams for {id}")
        return direct
