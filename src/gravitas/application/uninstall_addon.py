"""Use case: uninstall an addon by id."""

from __future__ import annotations

from gravitas.application.addon_repository import AddonRepository


class UninstallAddon:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, addon_id: str) -> None:
        self._repo.uninstall(addon_id)
