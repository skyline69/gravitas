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


async def test_relative_url_becomes_addon_unreachable() -> None:
    # A user pastes "/dasdsas" into an Add field. With cookies in the shared
    # jar (any earlier response can set one), httpx raises a plain ValueError
    # while BUILDING the request — from urllib inside its cookie-header
    # compat, before its own URL validation — which is not an httpx.HTTPError:
    # unmapped, it escapes the asyncSlot as a crash at quit.
    async with httpx.AsyncClient(cookies={"session": "x"}) as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable):
            await client.fetch_manifest("/dasdsas")


async def test_garbage_url_becomes_addon_unreachable() -> None:
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable):
            await client.fetch_manifest("not a url at all")


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


# --- disk cache layer ---------------------------------------------------------

_CATALOG_JSON = {"metas": [{"id": "tt1", "type": "movie", "name": "A", "poster": "p"}]}
_CATALOG_URL = "https://cin.strem.io/catalog/movie/top.json"


def _manifest() -> AddonManifest:
    return AddonManifest(
        id="c",
        name="C",
        version="1",
        resources=(ResourceSpec(name="catalog", types=("movie",)),),
        types=("movie",),
        catalogs=(CatalogRef(type="movie", id="top", name="T"),),
        base_url="https://cin.strem.io/",
    )


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _disk(tmp_path, clock):  # type: ignore[no-untyped-def]
    from gravitas.infrastructure.cache.json_disk_cache import JsonDiskCache

    return JsonDiskCache(tmp_path / "cache.db", clock=clock)


@respx.mock
async def test_fresh_disk_hit_skips_the_network(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = _Clock()
    disk = _disk(tmp_path, clock)
    disk.put(_CATALOG_URL, _CATALOG_JSON)
    clock.now += 60  # well inside CATALOG_TTL
    # No respx route for the URL: any network attempt would raise.
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        ref = CatalogRef(type="movie", id="top", name="T")
        items = await client.fetch_catalog(_manifest(), ref)
    assert items[0].id == "tt1"


@respx.mock
async def test_stale_disk_entry_refetches_normally(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = _Clock()
    disk = _disk(tmp_path, clock)
    disk.put(_CATALOG_URL, {"metas": []})  # stale content
    clock.now += AddonClient.CATALOG_TTL + 1
    route = respx.get(_CATALOG_URL).mock(return_value=httpx.Response(200, json=_CATALOG_JSON))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        ref = CatalogRef(type="movie", id="top", name="T")
        items = await client.fetch_catalog(_manifest(), ref)
    assert route.called
    assert items[0].id == "tt1"
    # The refetch refreshed the disk entry.
    hit = disk.get(_CATALOG_URL)
    assert hit is not None and hit[1] == 0


@respx.mock
async def test_serve_stale_returns_old_entry_without_network(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = _Clock()
    disk = _disk(tmp_path, clock)
    disk.put(_CATALOG_URL, _CATALOG_JSON)
    clock.now += AddonClient.CATALOG_TTL + 1
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        client.serve_stale = True
        ref = CatalogRef(type="movie", id="top", name="T")
        items = await client.fetch_catalog(_manifest(), ref)
    assert items[0].id == "tt1"


@respx.mock
async def test_stale_serving_never_poisons_the_memory_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Serve stale once, then flip the switch off: the next read must hit the
    # network, not a stale entry promoted into the in-memory cache.
    clock = _Clock()
    disk = _disk(tmp_path, clock)
    disk.put(_CATALOG_URL, {"metas": []})
    clock.now += AddonClient.CATALOG_TTL + 1
    route = respx.get(_CATALOG_URL).mock(return_value=httpx.Response(200, json=_CATALOG_JSON))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        client.serve_stale = True
        ref = CatalogRef(type="movie", id="top", name="T")
        stale = await client.fetch_catalog(_manifest(), ref)
        assert stale == []
        client.serve_stale = False
        fresh = await client.fetch_catalog(_manifest(), ref)
    assert route.called
    assert fresh[0].id == "tt1"


@respx.mock
async def test_network_response_lands_on_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = _Clock()
    disk = _disk(tmp_path, clock)
    respx.get(_CATALOG_URL).mock(return_value=httpx.Response(200, json=_CATALOG_JSON))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        ref = CatalogRef(type="movie", id="top", name="T")
        await client.fetch_catalog(_manifest(), ref)
    hit = disk.get(_CATALOG_URL)
    assert hit is not None and hit[0] == _CATALOG_JSON


@respx.mock
async def test_network_fetch_and_cache_hit_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c",
                "name": "C",
                "version": "1",
                "types": ["movie"],
                "resources": ["catalog"],
                "catalogs": [],
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with caplog.at_level("DEBUG", logger="gravitas.infrastructure.addons.client"):
            await client.fetch_manifest("https://cin.strem.io/manifest.json")
            await client.fetch_manifest("https://cin.strem.io/manifest.json")
    assert "GET https://cin.strem.io/manifest.json -> 200" in caplog.text
    assert "cache hit (memory): https://cin.strem.io/manifest.json" in caplog.text


# --- what a rejected request tells the user -----------------------------------


@respx.mock
async def test_status_error_carries_the_addons_own_message() -> None:
    # AIOStreams answers a manifest for a config it no longer accepts with a
    # 400 whose body says exactly what is wrong. httpx's own string for the
    # status says only "Client error '400 Bad Request'" and repeats the URL,
    # so the sentence the user needs has to come out of the body.
    respx.get("https://aio/stremio/tok/manifest.json").mock(
        return_value=httpx.Response(
            400,
            json={
                "success": False,
                "error": {
                    "code": "USER_INVALID_CONFIG",
                    "message": "Your stream expressions have 50037 characters, the max is 50000",
                },
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable) as caught:
            await client.fetch_manifest("https://aio/stremio/tok/manifest.json")
    message = str(caught.value)
    assert "HTTP 400" in message
    assert "Your stream expressions have 50037 characters, the max is 50000" in message
    # Which addon refused, without the token that identifies nothing to a user.
    assert "aio" in message
    assert "developer.mozilla.org" not in message


@respx.mock
async def test_status_error_message_stays_short_enough_to_read() -> None:
    # The toast that shows this caps at three short lines, so a message built
    # around a 200-character proxy URL elides before it reaches the reason.
    long_url = "https://aio/stremio/" + "t" * 300 + "/manifest.json"
    respx.get(long_url).mock(return_value=httpx.Response(502, content=b"<html>bad gateway</html>"))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable) as caught:
            await client.fetch_manifest(long_url)
    message = str(caught.value)
    assert len(message) <= 200
    assert "HTTP 502" in message
    # An HTML error page is not a sentence; the status has to stand alone.
    assert "<html>" not in message


@respx.mock
async def test_status_error_takes_a_plain_string_error_field() -> None:
    # Plenty of addons answer {"error": "..."} rather than nesting it.
    respx.get("https://addon/manifest.json").mock(
        return_value=httpx.Response(404, json={"error": "no such user"})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable) as caught:
            await client.fetch_manifest("https://addon/manifest.json")
    assert "no such user" in str(caught.value)


@respx.mock
async def test_transport_error_still_names_the_addon() -> None:
    respx.get("https://down/manifest.json").mock(side_effect=httpx.ConnectError("nope"))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable) as caught:
            await client.fetch_manifest("https://down/manifest.json")
    assert "https://down/manifest.json" in str(caught.value)
    assert "nope" in str(caught.value)


_STREAM_URL = "https://cin.strem.io/stream/movie/tt1.json"


@respx.mock
async def test_stream_answer_is_stored_but_never_served_as_fresh(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # Kept on disk as a placeholder for the next visit -- fetch_streams itself
    # still goes to the network every time, because the links may expire.
    disk = _disk(tmp_path, _Clock())
    route = respx.get(_STREAM_URL).mock(
        return_value=httpx.Response(200, json={"streams": [{"url": "http://v/1"}]})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        assert await client.stored_streams(_manifest(), "movie", "tt1") is None
        await client.fetch_streams(_manifest(), "movie", "tt1")
        await client.fetch_streams(_manifest(), "movie", "tt1")
        stored = await client.stored_streams(_manifest(), "movie", "tt1")
    assert route.call_count == 2
    assert stored is not None and [s.url for s in stored] == ["http://v/1"]


@respx.mock
async def test_a_refused_stream_request_keeps_the_last_good_answer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    disk = _disk(tmp_path, _Clock())
    disk.put(_STREAM_URL, {"streams": [{"url": "http://v/1"}]})
    respx.get(_STREAM_URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        with pytest.raises(AddonUnreachable):
            await client.fetch_streams(_manifest(), "movie", "tt1")
        stored = await client.stored_streams(_manifest(), "movie", "tt1")
    assert stored is not None and [s.url for s in stored] == ["http://v/1"]


_META_URL = "https://cin.strem.io/meta/movie/tt1.json"


@respx.mock
async def test_stored_meta_is_the_disk_copy_of_any_age(tmp_path) -> None:  # type: ignore[no-untyped-def]
    clock = _Clock()
    disk = _disk(tmp_path, clock)
    async with httpx.AsyncClient() as http:
        client = AddonClient(http, disk=disk)
        assert await client.stored_meta(_manifest(), "movie", "tt1") is None
        disk.put(_META_URL, {"meta": {"id": "tt1", "type": "movie", "name": "Old"}})
        clock.now += 30 * 24 * 60 * 60  # far past the day a fetch would trust it
        stored = await client.stored_meta(_manifest(), "movie", "tt1")
    assert stored is not None and stored.name == "Old"
