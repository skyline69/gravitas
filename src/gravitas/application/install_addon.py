"""Use case: install an addon by manifest URL."""

from __future__ import annotations

from gravitas.domain.models import AddonManifest
from gravitas.infrastructure.addons.repository import AddonRepository


class InstallAddon:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, url: str) -> AddonManifest:
        return await self._repo.install(url)
