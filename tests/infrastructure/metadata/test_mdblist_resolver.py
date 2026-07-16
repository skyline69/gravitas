import httpx
import pytest
import respx

from gravitas.domain.errors import MdbListUnavailable
from gravitas.infrastructure.metadata.mdblist_resolver import (
    MdbListResolver,
    _parse_ratings,
)

_PAYLOAD = {
    "ratings": [
        {"source": "imdb", "value": 8.0},
        {"source": "tomatoes", "value": 87},
        {"source": "audience", "value": 91},
        {"source": "letterboxd", "value": 4.1},
        {"source": "metacritic", "value": 74},
    ]
}


def test_parse_extracts_rt_and_letterboxd():
    r = _parse_ratings(_PAYLOAD)
    assert r.rotten_tomatoes == "87"
    assert r.rotten_tomatoes_fresh is True
    assert r.letterboxd == "4.1"


def test_parse_rotten_when_below_60():
    r = _parse_ratings({"ratings": [{"source": "tomatoes", "value": 42}]})
    assert r.rotten_tomatoes == "42"
    assert r.rotten_tomatoes_fresh is False


def test_parse_missing_sources_are_none():
    r = _parse_ratings({"ratings": [{"source": "imdb", "value": 8.0}]})
    assert r.rotten_tomatoes is None
    assert r.rotten_tomatoes_fresh is None
    assert r.letterboxd is None


def test_parse_null_and_malformed_values_are_none():
    r = _parse_ratings(
        {"ratings": [{"source": "tomatoes", "value": None}, {"source": "letterboxd"}]}
    )
    assert r.rotten_tomatoes is None
    assert r.letterboxd is None


def test_parse_no_ratings_key():
    assert _parse_ratings({}) == _parse_ratings({"ratings": "nope"})


@pytest.mark.asyncio
@respx.mock
async def test_ratings_fetches_and_caches():
    route = respx.get("https://api.mdblist.com/").mock(
        return_value=httpx.Response(200, json=_PAYLOAD)
    )
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        first = await resolver.ratings("tt1375666")
        second = await resolver.ratings("tt1375666")
    assert first.rotten_tomatoes == "87"
    assert first.letterboxd == "4.1"
    assert second == first
    assert route.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_ratings_without_key_raises():
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: None)
        with pytest.raises(MdbListUnavailable):
            await resolver.ratings("tt1375666")


@pytest.mark.asyncio
@respx.mock
async def test_ratings_http_error_raises_mdblist_unavailable():
    respx.get("https://api.mdblist.com/").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        with pytest.raises(MdbListUnavailable):
            await resolver.ratings("tt1375666")
