import asyncio
import contextlib

import pytest

from gravitas.application.addon_repository import AddonRepository, StreamFetch
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


class _CountingRepo:
    """Stands in for AddonRepository.fetch_streams: counts requests, and holds
    each one open until the test releases it."""

    def __init__(self, fetch: StreamFetch | None = None) -> None:

        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()
        self.fetch = fetch or StreamFetch(
            (Stream(name="s", title="t", url="http://v", info_hash=None, file_idx=None),),
            complete=True,
        )

    async def fetch_streams(self, type: MediaType, id: str) -> StreamFetch:
        self.calls += 1
        await self.release.wait()
        return self.fetch

    async def stored_streams(self, type: MediaType, id: str) -> list[Stream] | None:
        return [
            Stream(name="old", title="t", url="http://old", info_hash=None, file_idx=None),
            Stream(name="torrent", title="t", url=None, info_hash="h", file_idx=0),
        ]


def _resolver(repo: _CountingRepo, clock: list[float]) -> ResolveStream:
    return ResolveStream(repo, clock=lambda: clock[0])  # type: ignore[arg-type]


async def test_click_joins_a_prefetch_still_in_flight() -> None:
    repo = _CountingRepo()
    repo.release.clear()
    resolve = _resolver(repo, [0.0])
    resolve.prefetch("series", "tt1:1:1")
    click = asyncio.ensure_future(resolve("series", "tt1:1:1"))
    await asyncio.sleep(0)
    repo.release.set()
    assert len(await click) == 1
    assert repo.calls == 1


async def test_recent_streams_are_reused_then_refetched() -> None:
    repo = _CountingRepo()
    clock = [0.0]
    resolve = _resolver(repo, clock)
    await resolve("movie", "tt1")
    clock[0] = ResolveStream.REUSE_S
    await resolve("movie", "tt1")
    assert repo.calls == 1, "back to Sources within the window should not refetch"
    clock[0] = ResolveStream.REUSE_S * 2 + 1
    await resolve("movie", "tt1")
    assert repo.calls == 2, "an old list holds links that may have expired"


async def test_empty_or_partial_answers_are_not_reused() -> None:
    from gravitas.application.addon_repository import StreamFetch

    for fetch in (
        StreamFetch((), complete=True),
        StreamFetch(
            (Stream(name="s", title="t", url="http://v", info_hash=None, file_idx=None),),
            complete=False,
        ),
    ):
        repo = _CountingRepo(fetch)
        resolve = _resolver(repo, [0.0])
        for _ in range(2):
            with contextlib.suppress(NoStreams):
                await resolve("movie", "tt1")
        assert repo.calls == 2


async def test_a_caller_giving_up_does_not_cancel_the_shared_fetch() -> None:
    repo = _CountingRepo()
    repo.release.clear()
    resolve = _resolver(repo, [0.0])
    first = asyncio.ensure_future(resolve("series", "tt1:1:1"))
    await asyncio.sleep(0)
    first.cancel()  # the user picked another episode meanwhile
    second = asyncio.ensure_future(resolve("series", "tt1:1:1"))
    await asyncio.sleep(0)
    repo.release.set()
    assert len(await second) == 1
    assert repo.calls == 1


async def test_stored_list_stands_in_only_until_a_fetch_finishes() -> None:
    repo = _CountingRepo()
    resolve = _resolver(repo, [0.0])
    stored = await resolve.stored("movie", "tt1")
    assert [s.name for s in stored] == ["old"], "only playable rows stand in"
    await resolve("movie", "tt1")
    # Fresher than the disk, and __call__ answers from it at once.
    assert await resolve.stored("movie", "tt1") == []


async def test_speculative_fetches_are_capped() -> None:
    # A pointer drifting across a row of posters must not become a burst of
    # aggregator requests: guesses beyond the cap are dropped, not queued.
    repo = _CountingRepo()
    repo.release.clear()
    resolve = _resolver(repo, [0.0])
    for i in range(ResolveStream.MAX_SPECULATIVE + 3):
        resolve.speculate("movie", f"tt{i}")
    await asyncio.sleep(0)
    assert repo.calls == ResolveStream.MAX_SPECULATIVE

    # An explicit prefetch is not a guess, and is never dropped.
    resolve.prefetch("movie", "tt-clicked")
    await asyncio.sleep(0)
    assert repo.calls == ResolveStream.MAX_SPECULATIVE + 1

    # Once the guesses finish, there is room for new ones.
    repo.release.set()
    for _ in range(3):
        await asyncio.sleep(0)
    resolve.speculate("movie", "tt-next")
    await asyncio.sleep(0)
    assert repo.calls == ResolveStream.MAX_SPECULATIVE + 2


async def test_an_addon_that_did_not_answer_is_not_an_empty_title() -> None:
    from gravitas.domain.errors import SourcesUnavailable

    resolve = _resolver(_CountingRepo(StreamFetch((), complete=False)), [0.0])
    with pytest.raises(SourcesUnavailable):
        await resolve("movie", "tt1")
    # Still a NoStreams, so every caller that treats it as "empty" still does.
    resolve = _resolver(_CountingRepo(StreamFetch((), complete=True)), [0.0])
    with pytest.raises(NoStreams) as caught:
        await resolve("movie", "tt1")
    assert not isinstance(caught.value, SourcesUnavailable)


async def test_get_detail_shares_a_running_fetch() -> None:
    from gravitas.application.get_detail import GetDetail

    calls = 0
    gate = asyncio.Event()

    class Repo:
        async def meta(self, type: MediaType, id: str) -> MetaDetail:
            nonlocal calls
            calls += 1
            await gate.wait()
            return MetaDetail(
                id=id,
                type=type,
                name="A",
                description=None,
                poster=None,
                background=None,
                videos=(),
            )

    get = GetDetail(Repo())  # type: ignore[arg-type]
    hover = asyncio.ensure_future(get("movie", "tt1"))
    click = asyncio.ensure_future(get("movie", "tt1"))
    await asyncio.sleep(0)
    hover.cancel()  # the pointer moved on; the click still wants it
    gate.set()
    assert (await click).name == "A"
    assert calls == 1
    await get("movie", "tt1")
    assert calls == 2, "a finished fetch is the addon client's to cache, not this one's"


async def test_reinstalling_an_unchanged_addon_logs_nothing_new(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The boot installs every addon twice (cached pass, then revalidation); a
    # line repeated on every launch is one nobody reads.
    repo = AddonRepository(FakeSource())
    with caplog.at_level("INFO", logger="gravitas.application.addon_repository"):
        await repo.install("https://a/")
        await repo.install("https://a/")
    installs = [r for r in caplog.records if "installed addon" in r.getMessage()]
    assert len(installs) == 1


async def test_ignored_catalogs_are_reported_once_per_install(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ChannelsToo(FakeSource):
        async def fetch_manifest(self, url: str) -> AddonManifest:
            from dataclasses import replace

            return replace(await super().fetch_manifest(url), ignored_catalogs=("tv:iptv",))

    repo = AddonRepository(ChannelsToo())
    with caplog.at_level("INFO", logger="gravitas.application.addon_repository"):
        await repo.install("https://a/")
        await repo.install("https://a/")
    ignored = [r for r in caplog.records if "ignoring" in r.getMessage()]
    assert [r.getMessage() for r in ignored] == [
        "addon fake: ignoring 'tv' catalog 'iptv' (only movie/series are supported)"
    ]
