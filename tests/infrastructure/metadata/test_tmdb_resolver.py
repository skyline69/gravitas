import httpx
import pytest
import respx

from gravitas.domain.errors import TmdbUnavailable
from gravitas.infrastructure.metadata.tmdb_resolver import TmdbResolver


async def test_no_key_raises() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(TmdbUnavailable):
            await TmdbResolver(http, lambda: None).resolve("tvdb", "81189")


@respx.mock
async def test_resolve_tvdb_maps_to_imdb() -> None:
    respx.get("https://api.themoviedb.org/3/find/81189").mock(
        return_value=httpx.Response(
            200,
            json={
                "movie_results": [],
                "tv_results": [
                    {
                        "id": 1396,
                        "name": "Breaking Bad",
                        "poster_path": "/p.jpg",
                        "first_air_date": "2008-01-20",
                    }
                ],
            },
        )
    )
    respx.get("https://api.themoviedb.org/3/tv/1396/external_ids").mock(
        return_value=httpx.Response(200, json={"imdb_id": "tt0903747"})
    )
    async with httpx.AsyncClient() as http:
        r = await TmdbResolver(http, lambda: "KEY").resolve("tvdb", "81189")
    assert r.imdb_id == "tt0903747"
    assert r.type == "series"
    assert r.name == "Breaking Bad"
    assert r.year == "2008"
    assert r.poster == "https://image.tmdb.org/t/p/w342/p.jpg"


@respx.mock
async def test_resolve_no_results_raises() -> None:
    respx.get("https://api.themoviedb.org/3/find/999").mock(
        return_value=httpx.Response(200, json={"movie_results": [], "tv_results": []})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(TmdbUnavailable):
            await TmdbResolver(http, lambda: "KEY").resolve("tvdb", "999")


@respx.mock
async def test_resolve_http_error_raises() -> None:
    respx.get("https://api.themoviedb.org/3/find/1").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TmdbUnavailable):
            await TmdbResolver(http, lambda: "BAD").resolve("tvdb", "1")
