import httpx
import pytest
import respx

from gravitas.domain.errors import AddonUnreachable, InvalidResponse
from gravitas.domain.models import AddonManifest, CatalogRef, ExtraSpec, ResourceSpec
from gravitas.infrastructure.addons.client import AddonClient


@respx.mock
async def test_fetch_manifest_strips_suffix() -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c",
                "name": "Cinemeta",
                "version": "3.0",
                "types": ["movie"],
                "resources": ["catalog"],
                "catalogs": [],
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        manifest = await client.fetch_manifest("https://cin.strem.io/manifest.json")
    assert manifest.base_url == "https://cin.strem.io/"


@respx.mock
async def test_fetch_catalog_builds_path() -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c",
                "name": "C",
                "version": "1",
                "types": ["movie"],
                "resources": ["catalog"],
                "catalogs": [{"type": "movie", "id": "top", "name": "T"}],
            },
        )
    )
    respx.get("https://cin.strem.io/catalog/movie/top.json").mock(
        return_value=httpx.Response(
            200, json={"metas": [{"id": "tt1", "type": "movie", "name": "A", "poster": "p"}]}
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        manifest = await client.fetch_manifest("https://cin.strem.io/manifest.json")
        items = await client.fetch_catalog(manifest, CatalogRef(type="movie", id="top", name="T"))
    assert items[0].id == "tt1"


@respx.mock
async def test_transport_error_becomes_addon_unreachable() -> None:
    respx.get("https://down/manifest.json").mock(side_effect=httpx.ConnectError("nope"))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable):
            await client.fetch_manifest("https://down/manifest.json")


@respx.mock
async def test_non_json_body_becomes_invalid_response() -> None:
    respx.get("https://bad/manifest.json").mock(
        return_value=httpx.Response(200, content=b"<html>not json</html>")
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(InvalidResponse):
            await client.fetch_manifest("https://bad/manifest.json")


@respx.mock
async def test_fetch_catalog_builds_extra_path() -> None:
    respx.get("https://a/catalog/movie/top/genre=Action&skip=100.json").mock(
        return_value=httpx.Response(
            200, json={"metas": [{"id": "tt1", "type": "movie", "name": "A"}]}
        )
    )
    manifest = AddonManifest(
        id="a",
        name="A",
        version="1",
        resources=(ResourceSpec(name="catalog"),),
        types=("movie",),
        catalogs=(CatalogRef(type="movie", id="top", name="T"),),
        base_url="https://a/",
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        items = await client.fetch_catalog(manifest, manifest.catalogs[0], genre="Action", skip=100)
    assert items[0].id == "tt1"


@respx.mock
async def test_fetch_catalog_search_path() -> None:
    ref = CatalogRef(type="movie", id="top", name="Top", extra=(ExtraSpec(name="search"),))
    manifest = AddonManifest(
        id="c",
        name="C",
        version="1",
        resources=(ResourceSpec(name="catalog"),),
        types=("movie",),
        catalogs=(ref,),
        base_url="https://cin.strem.io/",
    )
    route = respx.get("https://cin.strem.io/catalog/movie/top/search=batman.json").mock(
        return_value=httpx.Response(200, json={"metas": []})
    )
    async with httpx.AsyncClient() as http:
        await AddonClient(http).fetch_catalog(manifest, ref, search="batman")
    assert route.called


# --- response caching --------------------------------------------------------

_MANIFEST_JSON = {
    "id": "c",
    "name": "Cinemeta",
    "version": "3.0",
    "types": ["movie"],
    "resources": ["catalog", "meta", "stream"],
    "catalogs": [{"type": "movie", "id": "top", "name": "Popular"}],
}

_MANIFEST = AddonManifest(
    id="c",
    name="Cinemeta",
    version="3.0",
    base_url="https://cin.strem.io/",
    types=("movie",),
    resources=(
        ResourceSpec(name="catalog"),
        ResourceSpec(name="meta"),
        ResourceSpec(name="stream"),
    ),
    catalogs=(CatalogRef(type="movie", id="top", name="Popular"),),
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@respx.mock
async def test_meta_is_served_from_cache_on_a_second_open() -> None:
    route = respx.get("https://cin.strem.io/meta/movie/tt1.json").mock(
        return_value=httpx.Response(200, json={"meta": {"id": "tt1", "type": "movie", "name": "M"}})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        await client.fetch_meta(_MANIFEST, "movie", "tt1")
        await client.fetch_meta(_MANIFEST, "movie", "tt1")
    # Reopening a title must not cost a second round-trip.
    assert route.call_count == 1


@respx.mock
async def test_streams_are_never_cached() -> None:
    route = respx.get("https://cin.strem.io/stream/movie/tt1.json").mock(
        return_value=httpx.Response(200, json={"streams": []})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        await client.fetch_streams(_MANIFEST, "movie", "tt1")
        await client.fetch_streams(_MANIFEST, "movie", "tt1")
    # Addon and debrid links are frequently time-limited or single-use. A
    # cached one fails playback with no obvious cause.
    assert route.call_count == 2


@respx.mock
async def test_catalog_refetches_once_its_ttl_expires() -> None:
    route = respx.get("https://cin.strem.io/catalog/movie/top.json").mock(
        return_value=httpx.Response(200, json={"metas": []})
    )
    clock = FakeClock()
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, clock=clock)
        ref = _MANIFEST.catalogs[0]
        await client.fetch_catalog(_MANIFEST, ref)
        await client.fetch_catalog(_MANIFEST, ref)
        assert route.call_count == 1
        clock.now = AddonClient.CATALOG_TTL
        await client.fetch_catalog(_MANIFEST, ref)
    assert route.call_count == 2


@respx.mock
async def test_meta_outlives_a_catalog_ttl() -> None:
    meta = respx.get("https://cin.strem.io/meta/movie/tt1.json").mock(
        return_value=httpx.Response(200, json={"meta": {"id": "tt1", "type": "movie", "name": "M"}})
    )
    clock = FakeClock()
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, clock=clock)
        await client.fetch_meta(_MANIFEST, "movie", "tt1")
        clock.now = AddonClient.CATALOG_TTL * 2
        await client.fetch_meta(_MANIFEST, "movie", "tt1")
    # Meta barely changes; it must not share the catalog's short lifetime.
    assert meta.call_count == 1


@respx.mock
async def test_different_urls_do_not_share_an_entry() -> None:
    one = respx.get("https://cin.strem.io/meta/movie/tt1.json").mock(
        return_value=httpx.Response(200, json={"meta": {"id": "tt1", "type": "movie", "name": "A"}})
    )
    two = respx.get("https://cin.strem.io/meta/movie/tt2.json").mock(
        return_value=httpx.Response(200, json={"meta": {"id": "tt2", "type": "movie", "name": "B"}})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        first = await client.fetch_meta(_MANIFEST, "movie", "tt1")
        second = await client.fetch_meta(_MANIFEST, "movie", "tt2")
    assert (first.name, second.name) == ("A", "B")
    assert (one.call_count, two.call_count) == (1, 1)


@respx.mock
async def test_a_search_is_cached_separately_from_the_plain_catalog() -> None:
    plain = respx.get("https://cin.strem.io/catalog/movie/top.json").mock(
        return_value=httpx.Response(200, json={"metas": []})
    )
    search = respx.get("https://cin.strem.io/catalog/movie/top/search=dune.json").mock(
        return_value=httpx.Response(200, json={"metas": []})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        ref = _MANIFEST.catalogs[0]
        await client.fetch_catalog(_MANIFEST, ref)
        await client.fetch_catalog(_MANIFEST, ref, search="dune")
    # The cache is keyed by URL, and the query is part of it.
    assert (plain.call_count, search.call_count) == (1, 1)


@respx.mock
async def test_a_failure_is_not_cached() -> None:
    route = respx.get("https://cin.strem.io/meta/movie/tt1.json").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"meta": {"id": "tt1", "type": "movie", "name": "M"}}),
        ]
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable):
            await client.fetch_meta(_MANIFEST, "movie", "tt1")
        # A dead addon must not poison the cache: the retry has to reach out.
        detail = await client.fetch_meta(_MANIFEST, "movie", "tt1")
    assert detail.name == "M"
    assert route.call_count == 2
