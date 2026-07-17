from types import SimpleNamespace
from typing import Any

from gravitas.application.trakt_account import TraktAccount
from gravitas.application.trakt_rows import (
    HISTORY_TITLE,
    RECOMMENDED_MOVIES_TITLE,
    RECOMMENDED_SERIES_TITLE,
    ROW_LIMIT,
    TraktRows,
)
from gravitas.domain.errors import GravitasError, TraktError
from gravitas.domain.models import MediaType, TraktAuth, TraktHistoryItem, TraktListItem

NOW = 1_700_000_000
FRESH = TraktAuth("ACCESS", "REFRESH", NOW + 90 * 86_400, "sky")


def _movie(i: int) -> TraktListItem:
    return TraktListItem(media_type="movie", imdb_id=f"tt{i}", title=f"Movie {i}")


def _show(i: int) -> TraktListItem:
    return TraktListItem(media_type="series", imdb_id=f"tt{i}", title=f"Show {i}")


def _movie_play(i: int) -> TraktHistoryItem:
    return TraktHistoryItem(
        media_type="movie", imdb_id=f"tt{i}", title=f"Movie {i}", watched_at=NOW - i
    )


def _show_play(i: int) -> TraktHistoryItem:
    return TraktHistoryItem(
        media_type="series",
        imdb_id=f"tt{i}",
        title=f"Show {i}",
        watched_at=NOW - i,
        season=1,
        episode=1,
    )


class FakeApi:
    def __init__(self) -> None:
        self.movie_recs: list[TraktListItem] = []
        self.show_recs: list[TraktListItem] = []
        self.history_entries: list[TraktHistoryItem] = []
        self.errors: dict[str, TraktError] = {}  # keyed "movie"/"series"/"history"

    async def recommendations(
        self, client_id: str, access_token: str, media_type: MediaType, limit: int
    ) -> list[TraktListItem]:
        if media_type in self.errors:
            raise self.errors[media_type]
        return list(self.movie_recs if media_type == "movie" else self.show_recs)

    async def history(
        self, client_id: str, access_token: str, limit: int
    ) -> list[TraktHistoryItem]:
        if "history" in self.errors:
            raise self.errors["history"]
        return list(self.history_entries)


class FakeGetDetail:
    def __init__(self) -> None:
        self.posters: dict[str, str] = {}
        self.calls: list[str] = []

    async def __call__(self, type: MediaType, id: str) -> Any:
        self.calls.append(id)
        if id not in self.posters:
            raise GravitasError(f"no meta for {id}")
        return SimpleNamespace(poster=self.posters[id])


def make_rows(auth: TraktAuth | None = FRESH) -> tuple[TraktRows, FakeApi, FakeGetDetail]:
    api = FakeApi()
    account = TraktAccount(api, clock=lambda: NOW)  # type: ignore[arg-type]
    account.client_id = "CID"
    account.client_secret = "SEC"
    account.auth = auth
    detail = FakeGetDetail()
    return TraktRows(account, detail), api, detail  # type: ignore[arg-type]


async def test_disconnected_returns_no_rows() -> None:
    rows, api, _ = make_rows(auth=None)
    api.movie_recs = [_movie(1)]
    assert await rows() == []


async def test_builds_all_three_rows_in_order() -> None:
    rows, api, detail = make_rows()
    api.movie_recs = [_movie(1)]
    api.show_recs = [_show(2)]
    api.history_entries = [_movie_play(3), _show_play(4)]
    detail.posters = {"tt1": "p1", "tt2": "p2", "tt3": "p3", "tt4": "p4"}
    result = await rows()
    assert [(r.title, r.type) for r in result] == [
        (RECOMMENDED_MOVIES_TITLE, "movie"),
        (RECOMMENDED_SERIES_TITLE, "series"),
        (HISTORY_TITLE, ""),
    ]
    movies, shows, history = result
    assert [i.id for i in movies.items] == ["tt1"]
    assert movies.items[0].poster == "p1"
    assert movies.items[0].name == "Movie 1"
    assert movies.items[0].type == "movie"
    assert [i.id for i in shows.items] == ["tt2"]
    # History keeps mixed types per item.
    assert [(i.id, i.type) for i in history.items] == [("tt3", "movie"), ("tt4", "series")]


async def test_missing_poster_is_not_fatal() -> None:
    rows, api, detail = make_rows()
    api.movie_recs = [_movie(1)]
    detail.posters = {}  # every lookup fails
    (movies,) = await rows()
    assert movies.items[0].poster is None


async def test_history_dedupes_repeat_plays_and_caps_row_length() -> None:
    rows, api, _ = make_rows()
    api.history_entries = [_show_play(1), _show_play(1)] + [_movie_play(i) for i in range(2, 40)]
    (history,) = await rows()
    ids = [i.id for i in history.items]
    assert ids[0] == "tt1"
    assert len(ids) == len(set(ids)) == ROW_LIMIT


async def test_one_dead_endpoint_drops_only_its_row() -> None:
    rows, api, detail = make_rows()
    api.movie_recs = [_movie(1)]
    api.show_recs = [_show(2)]
    api.errors["series"] = TraktError("down", status=503)
    detail.posters = {"tt1": "p1"}
    result = await rows()
    assert [r.title for r in result] == [RECOMMENDED_MOVIES_TITLE]


async def test_empty_endpoint_drops_its_row() -> None:
    rows, api, _ = make_rows()
    api.movie_recs = [_movie(1)]
    result = await rows()
    assert [r.title for r in result] == [RECOMMENDED_MOVIES_TITLE]
