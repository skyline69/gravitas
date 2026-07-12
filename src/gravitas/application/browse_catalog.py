"""Use case: build catalog rows across all installed addons."""

from __future__ import annotations

from dataclasses import dataclass

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaItem, MediaType


@dataclass(frozen=True, slots=True)
class CatalogRow:
    title: str
    type: MediaType
    catalog_id: str
    items: list[MediaItem]


class BrowseCatalog:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self) -> list[CatalogRow]:
        rows: list[CatalogRow] = []
        for manifest, ref in self._repo.catalog_refs():
            items = await self._repo.aggregate_catalog(manifest, ref)
            rows.append(
                CatalogRow(
                    title=ref.name,
                    type=ref.type,
                    catalog_id=ref.id,
                    items=items,
                )
            )
        return rows
