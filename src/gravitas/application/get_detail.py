"""Use case: fetch full meta for one item."""

from __future__ import annotations

from gravitas.domain.models import AddonManifest, MediaType, MetaDetail
from gravitas.infrastructure.addons.repository import AddonRepository


class GetDetail:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        return await self._repo.meta(manifest, type, id)
