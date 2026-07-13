import pytest

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.errors import AddonRemovalError, AddonUnreachable
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)


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

    async def fetch_catalog(
        self,
        manifest: AddonManifest,
        ref: CatalogRef,
        *,
        genre: str | None = None,
        skip: int = 0,
        search: str | None = None,
    ) -> list[MediaItem]:
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


class CapabilitySource:
    """Two addons: a meta-only one (404s streams) and a stream-only one."""

    def _manifest(self, url: str) -> AddonManifest:
        if "meta" in url:
            resources: tuple[str, ...] = ("catalog", "meta")
        else:
            resources = ("stream",)
        return AddonManifest(
            id=url,
            name=url,
            version="1",
            resources=resources,
            types=("movie",),
            catalogs=(),
            base_url=url,
        )

    async def fetch_manifest(self, url: str) -> AddonManifest:
        return self._manifest(url)

    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        raise NotImplementedError

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        if "meta" not in manifest.id:
            raise AddonUnreachable("no movie meta here")
        return MetaDetail(
            id=id,
            type=type,
            name="Michael",
            description="d",
            poster=None,
            background=None,
            videos=(),
        )

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        if "stream" not in manifest.resources:
            raise AddonUnreachable("no streams here")
        return [
            Stream(
                name="1080p",
                title="web",
                url=f"http://{manifest.id}/v",
                info_hash=None,
                file_idx=None,
            )
        ]


async def test_meta_from_meta_addon_streams_from_stream_addon() -> None:
    repo = AddonRepository(CapabilitySource())
    await repo.install("https://meta-addon/")
    await repo.install("https://stream-addon/")

    meta = await repo.meta("movie", "tt1")
    assert meta.name == "Michael"  # came from the meta addon, not the stream one

    streams = await repo.streams("movie", "tt1")
    assert [s.url for s in streams] == ["http://https://stream-addon//v"]


async def test_streams_aggregate_and_dedupe_across_addons() -> None:
    class TwoStreamAddons:
        async def fetch_manifest(self, url: str) -> AddonManifest:
            return AddonManifest(
                id=url,
                name=url,
                version="1",
                resources=("stream",),
                types=("movie",),
                catalogs=(),
                base_url=url,
            )

        async def fetch_catalog(self, manifest, ref):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def fetch_meta(self, manifest, type, id):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def fetch_streams(self, manifest, type, id):  # type: ignore[no-untyped-def]
            # both addons return a shared url plus a unique one
            return [
                Stream(
                    name="shared", title="t", url="http://shared/v", info_hash=None, file_idx=None
                ),
                Stream(
                    name=manifest.id,
                    title="t",
                    url=f"http://{manifest.id}/v",
                    info_hash=None,
                    file_idx=None,
                ),
            ]

    repo = AddonRepository(TwoStreamAddons())
    await repo.install("https://a/")
    await repo.install("https://b/")
    streams = await repo.streams("movie", "tt1")
    urls = sorted(s.url or "" for s in streams)
    assert urls == [
        "http://https://a//v",
        "http://https://b//v",
        "http://shared/v",
    ]  # shared de-duped


async def test_meta_raises_when_no_meta_addon() -> None:
    class StreamOnly:
        async def fetch_manifest(self, url: str) -> AddonManifest:
            return AddonManifest(
                id=url,
                name=url,
                version="1",
                resources=("stream",),
                types=("movie",),
                catalogs=(),
                base_url=url,
            )

        async def fetch_catalog(self, manifest, ref):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def fetch_meta(self, manifest, type, id):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        async def fetch_streams(self, manifest, type, id):  # type: ignore[no-untyped-def]
            return []

    repo = AddonRepository(StreamOnly())
    await repo.install("https://a/")
    with pytest.raises(AddonUnreachable):
        await repo.meta("movie", "tt1")


async def test_uninstall_removes_manifest() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    repo.uninstall(manifest.id)
    assert repo.installed() == []


async def test_uninstall_protected_raises() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/", protected=True)
    assert repo.is_protected(manifest.id) is True
    with pytest.raises(AddonRemovalError):
        repo.uninstall(manifest.id)
    assert repo.installed() != []


async def test_uninstall_absent_raises() -> None:
    repo = AddonRepository(FakeSource())
    with pytest.raises(AddonRemovalError):
        repo.uninstall("nope")


async def test_user_addon_urls_tracks_installs_and_removals() -> None:
    repo = AddonRepository(FakeSource())
    await repo.install("https://default/", protected=True)
    await repo.install("https://a/")
    await repo.install("https://b/")
    # protected addons are excluded: bootstrap reinstalls them itself
    assert repo.user_addon_urls() == ["https://a/", "https://b/"]
    repo.uninstall("https://a/")
    assert repo.user_addon_urls() == ["https://b/"]


async def test_user_addon_urls_reinstall_keeps_latest_url() -> None:
    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    await repo.install("https://a/")  # same id installed twice
    assert repo.user_addon_urls() == ["https://a/"]


async def test_install_default_not_protected() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    assert repo.is_protected(manifest.id) is False


async def test_search_aggregates_searchable_catalogs_dedup() -> None:
    class SearchSource(FakeSource):
        async def fetch_catalog(self, manifest, ref, *, genre=None, skip=0, search=None):  # type: ignore[override]
            if not search:
                return []
            return [
                MediaItem(id="tt1", type="movie", name=f"{search}-1", poster=None),
                MediaItem(id="tt1", type="movie", name="dup", poster=None),
                MediaItem(id="tt2", type="movie", name=f"{search}-2", poster=None),
            ]

    repo = AddonRepository(SearchSource())
    await repo.install("https://a/")  # FakeSource manifest catalog 'top' — mark searchable below
    # Rebuild an installed manifest whose catalog supports search:
    manifest = AddonManifest(
        id="s",
        name="S",
        version="1",
        resources=("catalog",),
        types=("movie",),
        catalogs=(CatalogRef(type="movie", id="top", name="Top", supports_search=True),),
        base_url="https://a/",
    )
    repo._manifests = [manifest]  # test-only: inject a searchable manifest
    results = await repo.search("matrix")
    ids = [r.id for r in results]
    assert ids == ["tt1", "tt2"]  # deduped by id, order preserved


async def test_search_skips_non_searchable_and_faults() -> None:
    class Boom(FakeSource):
        async def fetch_catalog(self, manifest, ref, *, genre=None, skip=0, search=None):  # type: ignore[override]
            raise AddonUnreachable("down")

    repo = AddonRepository(Boom())
    repo._manifests = [
        AddonManifest(
            id="s",
            name="S",
            version="1",
            resources=("catalog",),
            types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top", supports_search=True),),
            base_url="https://a/",
        )
    ]
    assert await repo.search("x") == []  # fault-isolated -> empty, no raise
