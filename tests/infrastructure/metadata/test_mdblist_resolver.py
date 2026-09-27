import httpx
import pytest
import respx

from gravitas.domain.errors import MdbListUnavailable
from gravitas.infrastructure.metadata.mdblist_resolver import (
    MdbListResolver,
    _parse_ratings,
)

# Recorded from a real MDBList response (GET /imdb/movie/tt1375666), trimmed.
_PAYLOAD = {
    "title": "Inception",
    "type": "movie",
    "ratings": [
        {"source": "imdb", "value": 8.8, "score": 88, "votes": 2833257},
        {"source": "metacritic", "value": 74, "score": 74, "votes": 42},
        {"source": "trakt", "value": 87, "score": 87, "votes": 62893},
        {"source": "tomatoes", "value": 87, "score": 87, "votes": 363, "fresh": 1},
        {"source": "popcorn", "value": 91, "score": 91, "votes": 41583},
        {"source": "tmdb", "value": 83, "score": 83, "votes": 39538},
        {"source": "letterboxd", "value": 4.2, "score": 84, "votes": 4152176},
        {"source": "myanimelist", "value": None, "score": None, "votes": None},
    ],
}


def test_parse_extracts_rt_and_letterboxd():
    r = _parse_ratings(_PAYLOAD)
    assert r.rotten_tomatoes == "87"
    assert r.rotten_tomatoes_fresh is True
    assert r.letterboxd == "4.2"


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


def test_parse_rejects_boolean_values():
    r = _parse_ratings(
        {
            "ratings": [
                {"source": "tomatoes", "value": True},
                {"source": "letterboxd", "value": False},
            ]
        }
    )
    assert r.rotten_tomatoes is None
    assert r.letterboxd is None


def test_parse_fresh_at_boundary_60():
    r = _parse_ratings({"ratings": [{"source": "tomatoes", "value": 60}]})
    assert r.rotten_tomatoes == "60"
    assert r.rotten_tomatoes_fresh is True


@pytest.mark.asyncio
@respx.mock
async def test_ratings_requests_movie_path_and_caches():
    route = respx.get("https://api.mdblist.com/imdb/movie/tt1375666").mock(
        return_value=httpx.Response(200, json=_PAYLOAD)
    )
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        first = await resolver.ratings("tt1375666", "movie")
        second = await resolver.ratings("tt1375666", "movie")
    assert first.rotten_tomatoes == "87"
    assert first.letterboxd == "4.2"
    assert second == first
    assert route.call_count == 1  # second call served from cache
    assert route.calls[0].request.url.params["apikey"] == "KEY"


@pytest.mark.asyncio
@respx.mock
async def test_ratings_requests_show_path_for_series():
    route = respx.get("https://api.mdblist.com/imdb/show/tt0903747").mock(
        return_value=httpx.Response(200, json={"ratings": [{"source": "tomatoes", "value": 96}]})
    )
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        r = await resolver.ratings("tt0903747", "series")
    assert route.called
    assert r.rotten_tomatoes == "96"


@pytest.mark.asyncio
async def test_ratings_without_key_raises():
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: None)
        with pytest.raises(MdbListUnavailable):
            await resolver.ratings("tt1375666", "movie")


@pytest.mark.asyncio
@respx.mock
async def test_ratings_http_error_raises_mdblist_unavailable():
    respx.get("https://api.mdblist.com/imdb/movie/tt1375666").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        with pytest.raises(MdbListUnavailable):
            await resolver.ratings("tt1375666", "movie")
