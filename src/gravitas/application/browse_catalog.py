"""Use case: build catalog rows across all installed addons."""

from __future__ import annotations

from dataclasses import dataclass

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaItem, MediaType


@dataclass(frozen=True, slots=True)
class CatalogRow:
    title: str
    addon_id: str
    type: MediaType
    catalog_id: str
    items: list[MediaItem]


_TYPE_LABEL: dict[str, str] = {"movie": "Movies", "series": "Series"}


def _row_title(name: str, type: MediaType) -> str:
    """Disambiguate by type: addons reuse one catalog name across types
    (Cinemeta ships "Popular" for both movie and series), which rendered as
    duplicate rows. Skip the suffix when the name already says it."""
    label = _TYPE_LABEL[type]
    if label.lower() in name.lower():
        return name
    return f"{name} {label}"


class BrowseCatalog:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self) -> list[CatalogRow]:
        rows: list[CatalogRow] = []
        for manifest, ref in self._repo.catalog_refs():
            items = await self._repo.aggregate_catalog(manifest, ref)
            rows.append(
                CatalogRow(
                    title=_row_title(ref.name, ref.type),
                    addon_id=manifest.id,
                    type=ref.type,
                    catalog_id=ref.id,
                    items=items,
                )
            )
        return rows
