from dataclasses import replace

import pytest

from gravitas.application.trakt_account import TraktAccount
from gravitas.application.trakt_sync import TraktSync
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import TraktError
from gravitas.domain.models import TraktAuth, TraktPlayback

NOW = 1_700_000_000
AUTH = TraktAuth("ACCESS", "REFRESH", NOW + 90 * 86_400, "sky")

MOVIE = TraktPlayback(
    media_type="movie",
    imdb_id="tt1375666",
    title="Inception",
    progress=50.0,
    paused_at=NOW - 100,
    runtime_minutes=148,
)
EPISODE = TraktPlayback(
    media_type="series",
    imdb_id="tt0898266",
    title="The Show",
    progress=65.0,
    paused_at=NOW - 50,
    season=2,
    episode=5,
    episode_title="The Pilot",
    runtime_minutes=42,
)


class MemoryStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], object] = {}

    def load_all(self):  # type: ignore[no-untyped-def]
        return list(self.rows.values())

    def save(self, entry):  # type: ignore[no-untyped-def]
        self.rows[(entry.media_id, entry.video_id)] = entry

    def delete(self, media_id, video_id=None):  # type: ignore[no-untyped-def]
        pass

    def delete_many(self, keys):  # type: ignore[no-untyped-def]
        pass

    def clear(self) -> None:
        self.rows.clear()


class FakeApi:
    def __init__(self, entries: list[TraktPlayback]) -> None:
        self.entries = entries

    async def playback(self, client_id: str, access_token: str) -> list[TraktPlayback]:
        return list(self.entries)


def make_sync(
    entries: list[TraktPlayback], auth: TraktAuth | None = AUTH
) -> tuple[TraktSync, WatchProgressRepository]:
    account = TraktAccount(FakeApi(entries), clock=lambda: NOW)  # type: ignore[arg-type]
    account.client_id = "CID"
    account.client_secret = "SEC"
    account.auth = auth
    progress = WatchProgressRepository(MemoryStore(), clock=lambda: NOW)  # type: ignore[arg-type]
    return TraktSync(account, progress), progress


async def test_sync_applies_movie_and_episode() -> None:
    sync, progress = make_sync([MOVIE, EPISODE])
    assert await sync() == 2

    movie = progress.get("tt1375666", "")
    assert movie is not None
    assert movie.position == pytest.approx(148 * 60 * 0.5)
    assert movie.duration == 148 * 60
    assert movie.name == "Inception"
    assert movie.updated_at == MOVIE.paused_at

    episode = progress.get("tt0898266", "tt0898266:2:5")
    assert episode is not None
    assert episode.type == "series"
    assert episode.label == "S2E5 · The Pilot"
    # Continue Watching picks both up.
    assert {e.media_id for e in progress.in_progress()} == {"tt1375666", "tt0898266"}


async def test_sync_never_clobbers_newer_local_progress() -> None:
    sync, progress = make_sync([MOVIE])
    progress.record(
        media_id="tt1375666",
        video_id="",
        type="movie",
        name="Inception",
        poster=None,
        label="",
        position=4000.0,
        duration=148 * 60.0,
        updated_at=MOVIE.paused_at + 500,  # local is fresher
    )
    assert await sync() == 0
    local = progress.get("tt1375666", "")
    assert local is not None and local.position == 4000.0


async def test_sync_overwrites_older_local_progress() -> None:
    sync, progress = make_sync([MOVIE])
    progress.record(
        media_id="tt1375666",
        video_id="",
        type="movie",
        name="Inception",
        poster=None,
        label="",
        position=100.0,
        duration=148 * 60.0,
        updated_at=MOVIE.paused_at - 500,  # Trakt is fresher
    )
    assert await sync() == 1
    local = progress.get("tt1375666", "")
    assert local is not None and local.position == pytest.approx(148 * 60 * 0.5)


async def test_sync_skips_entries_without_runtime() -> None:
    sync, progress = make_sync([replace(MOVIE, runtime_minutes=None)])
    assert await sync() == 0
    assert progress.get("tt1375666", "") is None


async def test_sync_skips_barely_started_entries() -> None:
    # 0.2% of 148min ≈ 18s < MIN_POSITION — a misclick on another device.
    sync, _progress = make_sync([replace(MOVIE, progress=0.2)])
    assert await sync() == 0


async def test_sync_disconnected_raises() -> None:
    sync, _progress = make_sync([MOVIE], auth=None)
    with pytest.raises(TraktError):
        await sync()


async def test_sync_high_progress_lands_as_watched() -> None:
    sync, progress = make_sync([replace(MOVIE, progress=95.0)])
    assert await sync() == 1
    assert progress.is_watched("tt1375666", "")


async def test_sync_caps_applied_entries() -> None:
    entries = [
        TraktPlayback(
            media_type="movie",
            imdb_id=f"tt{i:07d}",
            title=f"M{i}",
            progress=50.0,
            paused_at=NOW - i,
            runtime_minutes=100,
        )
        for i in range(60)
    ]
    sync, progress = make_sync(entries)
    assert await sync() == 30  # MAX_ENTRIES
    assert len(progress.in_progress()) == 30


class FakeDetail:
    def __init__(self, poster: str | None) -> None:
        self.poster = poster
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, type: str, id: str):  # type: ignore[no-untyped-def]
        self.calls.append((type, id))

        class _Meta:
            poster = self.poster

        return _Meta()


async def test_sync_enriches_poster_from_addon_meta() -> None:
    account = TraktAccount(FakeApi([MOVIE]), clock=lambda: NOW)  # type: ignore[arg-type]
    account.client_id = "CID"
    account.client_secret = "SEC"
    account.auth = AUTH
    progress = WatchProgressRepository(MemoryStore(), clock=lambda: NOW)  # type: ignore[arg-type]
    detail = FakeDetail("http://p/inception.jpg")
    sync = TraktSync(account, progress, detail)  # type: ignore[arg-type]
    await sync()
    local = progress.get("tt1375666", "")
    assert local is not None and local.poster == "http://p/inception.jpg"
    assert detail.calls == [("movie", "tt1375666")]
