"""Use case: read an addon's manifest without installing it."""

from __future__ import annotations

from gravitas.domain.models import AddonManifest
from gravitas.domain.ports import AddonSource


class PreviewAddon:
    """Fetch a manifest so the user can be shown what a link wants to install.

    Costs no extra round-trip in practice: AddonClient caches manifests for the
    session, so confirming the install reuses this very response.
    """

    def __init__(self, source: AddonSource) -> None:
        self._source = source

    async def __call__(self, url: str) -> AddonManifest:
        return await self._source.fetch_manifest(url)
