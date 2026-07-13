import pytest

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.browse_catalog import BrowseCatalog, CatalogRow
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import NoStreams
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)


class FakeSource:
    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id="fake",
            name="Fake",
            version="1",
            resources=("catalog", "meta", "stream"),
            types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
            base_url=url,
        )

    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        return [MediaItem(id="tt1", type="movie", name="A", poster=None)]

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        return MetaDetail(
            id=id, type=type, name="A", description="d", poster=None, background=None, videos=()
        )

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        return [
            Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None),
            Stream(name="720p", title="torr", url=None, info_hash="abc", file_idx=0),
        ]


async def _repo() -> AddonRepository:
    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    return repo


async def test_install_addon() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await InstallAddon(repo)("https://a/")
    assert manifest.id == "fake"


async def test_browse_catalog_builds_rows() -> None:
    repo = await _repo()
    rows = await BrowseCatalog(repo)()
    assert rows == [
        CatalogRow(
            title="Top",
            addon_id="fake",
            type="movie",
            catalog_id="top",
            items=[MediaItem(id="tt1", type="movie", name="A", poster=None)],
        )
    ]


async def test_get_detail() -> None:
    repo = await _repo()
    meta = await GetDetail(repo)("movie", "tt1")
    assert meta.name == "A"


async def test_resolve_stream_filters_to_direct() -> None:
    repo = await _repo()
    streams = await ResolveStream(repo)("movie", "tt1")
    assert len(streams) == 1
    assert streams[0].is_direct


async def test_resolve_stream_raises_when_no_direct() -> None:
    class NoDirect(FakeSource):
        async def fetch_streams(self, manifest, type, id):  # type: ignore[no-untyped-def]
            return [Stream(name="x", title="t", url=None, info_hash="h", file_idx=0)]

    repo = AddonRepository(NoDirect())
    await repo.install("https://a/")
    with pytest.raises(NoStreams):
        await ResolveStream(repo)("movie", "tt1")
