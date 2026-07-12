from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)
from gravitas.infrastructure.addons.repository import AddonRepository


class FakeSource:
    def __init__(self) -> None:
        self.fail_catalog = False

    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id=url,
            name="Fake",
            version="1",
            resources=("catalog",),
            types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
            base_url=url,
        )

    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        if self.fail_catalog:
            raise AddonUnreachable("down")
        return [MediaItem(id="tt1", type="movie", name="A", poster=None)]

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        raise NotImplementedError

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        raise NotImplementedError


async def test_install_stores_manifest() -> None:
    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    assert len(repo.installed()) == 1
    assert repo.catalog_refs()[0][1].id == "top"


async def test_aggregate_catalog_returns_items() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    ref = manifest.catalogs[0]
    items = await repo.aggregate_catalog(manifest, ref)
    assert items[0].id == "tt1"


async def test_failing_addon_yields_empty_not_raise() -> None:
    source = FakeSource()
    repo = AddonRepository(source)
    manifest = await repo.install("https://a/")
    source.fail_catalog = True
    items = await repo.aggregate_catalog(manifest, manifest.catalogs[0])
    assert items == []
