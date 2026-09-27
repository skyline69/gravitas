"""Use case: install an addon by manifest URL."""

from __future__ import annotations

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import AddonManifest


class InstallAddon:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, url: str, *, protected: bool = False) -> AddonManifest:
        return await self._repo.install(url, protected=protected)
