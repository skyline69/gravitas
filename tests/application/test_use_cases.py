import pytest

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.browse_catalog import BrowseCatalog, CatalogRow
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.resolve_media_link import ResolveMediaLink
from gravitas.application.resolve_stream import ResolveStream
from gravitas.application.search_media import SearchMedia
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.errors import AddonRemovalError, AddonUnreachable, NoStreams, TmdbUnavailable
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    ResolvedMedia,
    ResourceSpec,
    Stream,
)


class FakeSource:
    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id="fake",
            name="Fake",
            version="1",
            resources=(
                ResourceSpec(name="catalog"),
                ResourceSpec(name="meta"),
                ResourceSpec(name="stream"),
            ),
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
    # Row titles carry the media type: addons reuse one catalog name across
    # types (Cinemeta's "Popular" exists for movie AND series), which showed
    # as indistinguishable duplicate rows on Home.
    repo = await _repo()
    rows = await BrowseCatalog(repo)()
    assert rows == [
        CatalogRow(
            title="Top Movies",
            addon_id="fake",
            type="movie",
            catalog_id="top",
            items=[MediaItem(id="tt1", type="movie", name="A", poster=None)],
        )
    ]


async def test_browse_catalog_keeps_title_with_type_word() -> None:
    class TypedNameSource(FakeSource):
        async def fetch_manifest(self, url: str) -> AddonManifest:
            manifest = await super().fetch_manifest(url)
            return AddonManifest(
                id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                resources=manifest.resources,
                types=("movie", "series"),
                catalogs=(
                    CatalogRef(type="movie", id="top", name="Best Movies"),
                    CatalogRef(type="series", id="top", name="Top"),
                ),
                base_url=manifest.base_url,
            )

    repo = AddonRepository(TypedNameSource())
    await repo.install("https://a/")
    rows = await BrowseCatalog(repo)()
    assert [r.title for r in rows] == ["Best Movies", "Top Series"]


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


async def test_resolve_stream_logs_counts(caplog: pytest.LogCaptureFixture) -> None:
    repo = await _repo()
    with caplog.at_level("INFO", logger="gravitas.application.resolve_stream"):
        await ResolveStream(repo)("movie", "tt1")
    assert "resolved 1 playable streams for movie tt1 (1 dropped as not direct)" in caplog.text


async def test_resolve_stream_warns_when_torrent_only(caplog: pytest.LogCaptureFixture) -> None:
    class NoDirect(FakeSource):
        async def fetch_streams(self, manifest, type, id):  # type: ignore[no-untyped-def]
            return [Stream(name="x", title="t", url=None, info_hash="h", file_idx=0)]

    repo = AddonRepository(NoDirect())
    await repo.install("https://a/")
    with caplog.at_level("WARNING"), pytest.raises(NoStreams):
        await ResolveStream(repo)("movie", "tt1")
    assert "no playable streams for movie tt1 (1 torrent/external-only dropped)" in caplog.text


async def test_install_logs_addon_identity(caplog: pytest.LogCaptureFixture) -> None:
    repo = AddonRepository(FakeSource())
    with caplog.at_level("INFO"):
        await repo.install("https://a/", protected=True)
    assert "installed addon fake v1 (1 catalogs, protected)" in caplog.text


async def test_uninstall_addon_removes() -> None:
    from .test_addon_repository import FakeSource

    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    await UninstallAddon(repo)(manifest.id)
    assert repo.installed() == []


async def test_uninstall_addon_propagates_error() -> None:
    from .test_addon_repository import FakeSource

    repo = AddonRepository(FakeSource())
    with pytest.raises(AddonRemovalError):
        await UninstallAddon(repo)("nope")


class _FakeSearchRepo:
    def __init__(self, items: list) -> None:
        self._items = items
        self.calls: list[str] = []

    async def search(self, query: str) -> list:
        self.calls.append(query)
        return self._items


async def test_search_media_empty_query_returns_empty() -> None:
    repo = _FakeSearchRepo(["x"])
    assert await SearchMedia(repo)("   ") == []  # type: ignore[arg-type]
    assert repo.calls == []


async def test_search_media_delegates() -> None:
    items = [MediaItem(id="tt1", type="movie", name="A", poster=None)]
    repo = _FakeSearchRepo(items)
    assert await SearchMedia(repo)("matrix") == items  # type: ignore[arg-type]
    assert repo.calls == ["matrix"]


# ResolveMediaLink tests


def _meta(id_: str, type_: str) -> MetaDetail:
    return MetaDetail(
        id=id_,
        type=type_,  # type: ignore[arg-type]
        name="Title",
        description=None,
        poster="p",
        background=None,
        videos=(),
        year="1999",
    )


class _FakeMetaRepo:
    def __init__(self, movie: bool = True, series: bool = False) -> None:
        self._movie, self._series = movie, series

    async def meta(self, type: str, id: str) -> MetaDetail:
        if type == "movie" and self._movie:
            return _meta(id, "movie")
        if type == "series" and self._series:
            return _meta(id, "series")
        raise AddonUnreachable("no meta")


class _FakeResolver:
    def __init__(self, result: ResolvedMedia | None = None) -> None:
        self._result = result

    async def resolve(self, source: str, external_id: str) -> ResolvedMedia:
        if self._result is None:
            raise TmdbUnavailable("no key")
        return self._result


async def test_resolve_imdb_movie() -> None:
    item = await ResolveMediaLink(_FakeMetaRepo(movie=True), _FakeResolver())("imdb", "tt1")  # type: ignore[arg-type]
    assert item == MediaItem(id="tt1", type="movie", name="Title", poster="p", year="1999")


async def test_resolve_imdb_falls_back_to_series() -> None:
    repo = _FakeMetaRepo(movie=False, series=True)
    item = await ResolveMediaLink(repo, _FakeResolver())("imdb", "tt9")  # type: ignore[arg-type]
    assert item.type == "series"


async def test_resolve_imdb_none_raises() -> None:
    with pytest.raises(AddonUnreachable):
        repo = _FakeMetaRepo(movie=False, series=False)
        await ResolveMediaLink(repo, _FakeResolver())("imdb", "tt0")  # type: ignore[arg-type]


async def test_resolve_tvdb_via_resolver() -> None:
    resolved = ResolvedMedia(imdb_id="tt42", type="series", name="Show", poster="ps", year="2020")
    item = await ResolveMediaLink(_FakeMetaRepo(), _FakeResolver(resolved))("tvdb", "81189")  # type: ignore[arg-type]
    assert item == MediaItem(id="tt42", type="series", name="Show", poster="ps", year="2020")


async def test_resolve_tvdb_no_key_raises() -> None:
    with pytest.raises(TmdbUnavailable):
        await ResolveMediaLink(_FakeMetaRepo(), _FakeResolver(None))("tvdb", "1")  # type: ignore[arg-type]
