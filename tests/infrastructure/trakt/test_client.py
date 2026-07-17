import json

import httpx
import pytest
import respx

from gravitas.domain.errors import TraktError
from gravitas.infrastructure.trakt.client import TraktClient

_TOKEN_PAYLOAD = {
    "access_token": "ACCESS",
    "token_type": "bearer",
    "expires_in": 7776000,
    "refresh_token": "REFRESH",
    "scope": "public",
    "created_at": 1_700_000_000,
}


def _body(route: respx.Route, call: int = 0) -> dict[str, object]:
    return json.loads(route.calls[call].request.content)  # type: ignore[no-any-return]


@pytest.mark.asyncio
@respx.mock
async def test_device_code_parses_response() -> None:
    route = respx.post("https://api.trakt.tv/oauth/device/code").mock(
        return_value=httpx.Response(
            200,
            json={
                "device_code": "DEV",
                "user_code": "1A2B3C4D",
                "verification_url": "https://trakt.tv/activate",
                "expires_in": 600,
                "interval": 5,
            },
        )
    )
    async with httpx.AsyncClient() as http:
        code = await TraktClient(http).device_code("CID")
    assert code.user_code == "1A2B3C4D"
    assert code.device_code == "DEV"
    assert code.interval == 5
    assert code.expires_in == 600
    request = route.calls[0].request
    assert request.headers["trakt-api-key"] == "CID"
    assert request.headers["trakt-api-version"] == "2"


@pytest.mark.asyncio
@respx.mock
async def test_poll_pending_returns_none() -> None:
    respx.post("https://api.trakt.tv/oauth/device/token").mock(return_value=httpx.Response(400))
    async with httpx.AsyncClient() as http:
        assert await TraktClient(http).poll_device_token("CID", "SEC", "DEV") is None


@pytest.mark.asyncio
@respx.mock
async def test_poll_slow_down_also_returns_none() -> None:
    respx.post("https://api.trakt.tv/oauth/device/token").mock(return_value=httpx.Response(429))
    async with httpx.AsyncClient() as http:
        assert await TraktClient(http).poll_device_token("CID", "SEC", "DEV") is None


@pytest.mark.asyncio
@respx.mock
async def test_poll_success_returns_auth_with_expiry() -> None:
    route = respx.post("https://api.trakt.tv/oauth/device/token").mock(
        return_value=httpx.Response(200, json=_TOKEN_PAYLOAD)
    )
    async with httpx.AsyncClient() as http:
        auth = await TraktClient(http).poll_device_token("CID", "SEC", "DEV")
    assert auth is not None
    assert auth.access_token == "ACCESS"
    assert auth.refresh_token == "REFRESH"
    assert auth.expires_at == 1_700_000_000 + 7_776_000
    assert _body(route) == {"code": "DEV", "client_id": "CID", "client_secret": "SEC"}


@pytest.mark.asyncio
@respx.mock
async def test_poll_denied_raises_with_status() -> None:
    respx.post("https://api.trakt.tv/oauth/device/token").mock(return_value=httpx.Response(418))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError) as excinfo:
            await TraktClient(http).poll_device_token("CID", "SEC", "DEV")
    assert excinfo.value.status == 418


@pytest.mark.asyncio
@respx.mock
async def test_refresh_token_posts_grant() -> None:
    route = respx.post("https://api.trakt.tv/oauth/token").mock(
        return_value=httpx.Response(200, json=_TOKEN_PAYLOAD)
    )
    async with httpx.AsyncClient() as http:
        auth = await TraktClient(http).refresh_token("CID", "SEC", "OLD")
    assert auth.access_token == "ACCESS"
    body = _body(route)
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "OLD"


@pytest.mark.asyncio
@respx.mock
async def test_scrobble_movie_payload() -> None:
    route = respx.post("https://api.trakt.tv/scrobble/start").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).scrobble(
            "CID", "TOKEN", "start", imdb_id="tt1375666", season=None, episode=None, progress=12.3
        )
    body = _body(route)
    assert body == {"progress": 12.3, "movie": {"ids": {"imdb": "tt1375666"}}}
    assert route.calls[0].request.headers["Authorization"] == "Bearer TOKEN"


@pytest.mark.asyncio
@respx.mock
async def test_scrobble_episode_payload() -> None:
    route = respx.post("https://api.trakt.tv/scrobble/stop").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).scrobble(
            "CID", "TOKEN", "stop", imdb_id="tt0898266", season=2, episode=5, progress=97.0
        )
    body = _body(route)
    assert body["show"] == {"ids": {"imdb": "tt0898266"}}
    assert body["episode"] == {"season": 2, "number": 5}


@pytest.mark.asyncio
@respx.mock
async def test_scrobble_progress_clamped() -> None:
    route = respx.post("https://api.trakt.tv/scrobble/pause").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).scrobble(
            "CID", "TOKEN", "pause", imdb_id="tt1", season=None, episode=None, progress=140.0
        )
    assert _body(route)["progress"] == 100.0


@pytest.mark.asyncio
@respx.mock
async def test_scrobble_409_is_tolerated() -> None:
    respx.post("https://api.trakt.tv/scrobble/start").mock(
        return_value=httpx.Response(409, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).scrobble(
            "CID", "TOKEN", "start", imdb_id="tt1", season=None, episode=None, progress=1.0
        )  # must not raise


@pytest.mark.asyncio
@respx.mock
async def test_scrobble_401_raises_with_status() -> None:
    respx.post("https://api.trakt.tv/scrobble/start").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError) as excinfo:
            await TraktClient(http).scrobble(
                "CID", "TOKEN", "start", imdb_id="tt1", season=None, episode=None, progress=1.0
            )
    assert excinfo.value.status == 401


@pytest.mark.asyncio
@respx.mock
async def test_username_reads_profile() -> None:
    route = respx.get("https://api.trakt.tv/users/me").mock(
        return_value=httpx.Response(200, json={"username": "skyline", "name": "S"})
    )
    async with httpx.AsyncClient() as http:
        assert await TraktClient(http).username("CID", "TOKEN") == "skyline"
    assert route.calls[0].request.headers["Authorization"] == "Bearer TOKEN"


@pytest.mark.asyncio
@respx.mock
async def test_revoke_posts_token() -> None:
    route = respx.post("https://api.trakt.tv/oauth/revoke").mock(
        return_value=httpx.Response(200, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).revoke("CID", "SEC", "TOKEN")
    assert _body(route)["token"] == "TOKEN"


_PLAYBACK_PAYLOAD = [
    {
        "id": 13,
        "progress": 25.5,
        "paused_at": "2026-07-17T02:24:30.000Z",
        "type": "movie",
        "movie": {
            "title": "Inception",
            "year": 2010,
            "runtime": 148,
            "ids": {"trakt": 1, "imdb": "tt1375666"},
        },
    },
    {
        "id": 37,
        "progress": 65.0,
        "paused_at": "2026-07-16T20:00:00.000Z",
        "type": "episode",
        "episode": {"season": 2, "number": 5, "title": "The Pilot", "runtime": 42},
        "show": {"title": "The Show", "ids": {"imdb": "tt0898266"}},
    },
    # Unaddressable: no imdb id.
    {
        "progress": 10.0,
        "paused_at": "2026-07-15T10:00:00.000Z",
        "type": "movie",
        "movie": {"title": "Obscure", "ids": {"trakt": 99}},
    },
]


@pytest.mark.asyncio
@respx.mock
async def test_playback_parses_movies_and_episodes() -> None:
    route = respx.get("https://api.trakt.tv/sync/playback").mock(
        return_value=httpx.Response(200, json=_PLAYBACK_PAYLOAD)
    )
    async with httpx.AsyncClient() as http:
        entries = await TraktClient(http).playback("CID", "TOKEN")
    assert route.calls[0].request.url.params["extended"] == "full"
    assert len(entries) == 2  # the imdb-less movie is dropped
    movie, episode = entries
    assert movie.media_type == "movie"
    assert movie.imdb_id == "tt1375666"
    assert movie.title == "Inception"
    assert movie.progress == 25.5
    assert movie.runtime_minutes == 148
    assert movie.paused_at > 0
    assert movie.playback_id == 13
    assert episode.playback_id == 37
    assert episode.media_type == "series"
    assert episode.imdb_id == "tt0898266"
    assert (episode.season, episode.episode) == (2, 5)
    assert episode.episode_title == "The Pilot"
    assert episode.runtime_minutes == 42


@pytest.mark.asyncio
@respx.mock
async def test_playback_error_raises() -> None:
    respx.get("https://api.trakt.tv/sync/playback").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError) as excinfo:
            await TraktClient(http).playback("CID", "TOKEN")
    assert excinfo.value.status == 401


@pytest.mark.asyncio
@respx.mock
async def test_remove_playback_deletes_row() -> None:
    route = respx.delete("https://api.trakt.tv/sync/playback/13").mock(
        return_value=httpx.Response(204)
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).remove_playback("CID", "TOKEN", 13)
    assert route.called
    assert route.calls[0].request.headers["Authorization"] == "Bearer TOKEN"


@pytest.mark.asyncio
@respx.mock
async def test_remove_playback_tolerates_already_gone() -> None:
    respx.delete("https://api.trakt.tv/sync/playback/13").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as http:
        await TraktClient(http).remove_playback("CID", "TOKEN", 13)  # must not raise


@pytest.mark.asyncio
@respx.mock
async def test_remove_playback_error_raises() -> None:
    respx.delete("https://api.trakt.tv/sync/playback/13").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError):
            await TraktClient(http).remove_playback("CID", "TOKEN", 13)


@pytest.mark.asyncio
@respx.mock
async def test_recommendations_parses_movies() -> None:
    route = respx.get("https://api.trakt.tv/recommendations/movies").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"title": "Inception", "year": 2010, "ids": {"imdb": "tt1375666"}},
                {"title": "No Imdb", "ids": {"trakt": 99}},
            ],
        )
    )
    async with httpx.AsyncClient() as http:
        items = await TraktClient(http).recommendations("CID", "TOKEN", "movie", 20)
    assert route.calls[0].request.url.params["limit"] == "20"
    assert route.calls[0].request.headers["Authorization"] == "Bearer TOKEN"
    assert [(i.media_type, i.imdb_id, i.title) for i in items] == [
        ("movie", "tt1375666", "Inception")
    ]


@pytest.mark.asyncio
@respx.mock
async def test_recommendations_shows_hit_shows_endpoint() -> None:
    route = respx.get("https://api.trakt.tv/recommendations/shows").mock(
        return_value=httpx.Response(200, json=[{"title": "The Show", "ids": {"imdb": "tt2"}}])
    )
    async with httpx.AsyncClient() as http:
        items = await TraktClient(http).recommendations("CID", "TOKEN", "series", 10)
    assert route.called
    assert items[0].media_type == "series"


@pytest.mark.asyncio
@respx.mock
async def test_recommendations_error_raises() -> None:
    respx.get("https://api.trakt.tv/recommendations/movies").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError) as excinfo:
            await TraktClient(http).recommendations("CID", "TOKEN", "movie", 20)
    assert excinfo.value.status == 401


@pytest.mark.asyncio
@respx.mock
async def test_history_parses_plays_with_episode_refs_and_timestamps() -> None:
    respx.get("https://api.trakt.tv/sync/history").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "type": "episode",
                    "watched_at": "2026-07-17T02:24:30.000Z",
                    "episode": {"season": 2, "number": 5, "title": "The Pilot"},
                    "show": {"title": "The Show", "ids": {"imdb": "tt0898266"}},
                },
                {
                    "type": "movie",
                    "watched_at": "2026-07-16T20:00:00.000Z",
                    "movie": {"title": "Inception", "ids": {"imdb": "tt1375666"}},
                },
                # Unaddressable: no imdb id.
                {
                    "type": "movie",
                    "watched_at": "2026-07-15T10:00:00.000Z",
                    "movie": {"title": "Obscure", "ids": {"trakt": 99}},
                },
                # No watched_at: cannot be ordered against local activity.
                {"type": "movie", "movie": {"title": "Undated", "ids": {"imdb": "tt7"}}},
            ],
        )
    )
    async with httpx.AsyncClient() as http:
        items = await TraktClient(http).history("CID", "TOKEN", 60)
    assert len(items) == 2
    episode, movie = items
    # Episode plays carry the SHOW's imdb id and title.
    assert episode.media_type == "series"
    assert episode.imdb_id == "tt0898266"
    assert episode.title == "The Show"
    assert (episode.season, episode.episode) == (2, 5)
    assert episode.episode_title == "The Pilot"
    assert episode.watched_at > 0
    assert movie.media_type == "movie"
    assert movie.imdb_id == "tt1375666"
    assert movie.season is None and movie.episode is None
    assert movie.watched_at > 0


@pytest.mark.asyncio
@respx.mock
async def test_add_to_history_movie_payload() -> None:
    route = respx.post("https://api.trakt.tv/sync/history").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).add_to_history(
            "CID", "TOKEN", media_type="movie", imdb_id="tt1375666", season=None, episode=None
        )
    assert _body(route) == {"movies": [{"ids": {"imdb": "tt1375666"}}]}
    assert route.calls[0].request.headers["Authorization"] == "Bearer TOKEN"


@pytest.mark.asyncio
@respx.mock
async def test_add_to_history_episode_payload() -> None:
    route = respx.post("https://api.trakt.tv/sync/history").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).add_to_history(
            "CID", "TOKEN", media_type="series", imdb_id="tt0898266", season=2, episode=5
        )
    assert _body(route) == {
        "shows": [
            {
                "ids": {"imdb": "tt0898266"},
                "seasons": [{"number": 2, "episodes": [{"number": 5}]}],
            }
        ]
    }


@pytest.mark.asyncio
@respx.mock
async def test_add_to_history_whole_show_payload() -> None:
    route = respx.post("https://api.trakt.tv/sync/history").mock(
        return_value=httpx.Response(201, json={})
    )
    async with httpx.AsyncClient() as http:
        await TraktClient(http).add_to_history(
            "CID", "TOKEN", media_type="series", imdb_id="tt0898266", season=None, episode=None
        )
    assert _body(route) == {"shows": [{"ids": {"imdb": "tt0898266"}}]}


@pytest.mark.asyncio
@respx.mock
async def test_add_to_history_error_raises_with_status() -> None:
    respx.post("https://api.trakt.tv/sync/history").mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError) as excinfo:
            await TraktClient(http).add_to_history(
                "CID", "TOKEN", media_type="movie", imdb_id="tt1", season=None, episode=None
            )
    assert excinfo.value.status == 401


@pytest.mark.asyncio
@respx.mock
async def test_transport_failure_wrapped_in_trakt_error() -> None:
    respx.post("https://api.trakt.tv/oauth/device/code").mock(
        side_effect=httpx.ConnectError("no route")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(TraktError):
            await TraktClient(http).device_code("CID")
