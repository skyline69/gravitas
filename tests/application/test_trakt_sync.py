from dataclasses import replace

import pytest

from gravitas.application.trakt_account import TraktAccount
from gravitas.application.trakt_sync import TraktSync
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import TraktError
from gravitas.domain.models import TraktAuth, TraktHistoryItem, TraktPlayback

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

    def load_forgotten(self):  # type: ignore[no-untyped-def]
        return []

    def save_forgotten(self, media_id, video_id, deleted_at):  # type: ignore[no-untyped-def]
        pass

    def delete_many(self, keys):  # type: ignore[no-untyped-def]
        pass

    def clear(self) -> None:
        self.rows.clear()


class FakeApi:
    def __init__(
        self,
        entries: list[TraktPlayback],
        plays: list[TraktHistoryItem] | None = None,
    ) -> None:
        self.entries = entries
        self.plays = plays or []

    async def playback(self, client_id: str, access_token: str) -> list[TraktPlayback]:
        return list(self.entries)

    async def history(
        self, client_id: str, access_token: str, limit: int
    ) -> list[TraktHistoryItem]:
        return list(self.plays[:limit])


def make_sync(
    entries: list[TraktPlayback],
    auth: TraktAuth | None = AUTH,
    plays: list[TraktHistoryItem] | None = None,
) -> tuple[TraktSync, WatchProgressRepository]:
    account = TraktAccount(FakeApi(entries, plays), clock=lambda: NOW)  # type: ignore[arg-type]
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


async def test_sync_respects_a_newer_local_forget() -> None:
    # The user pulled MOVIE in once, then forgot it locally (tombstone at NOW,
    # after MOVIE.paused_at). The next sync must not resurrect it — this is
    # what keeps "Forget progress" authoritative even when the Trakt-side
    # removal is disabled or failed.
    sync, progress = make_sync([MOVIE, EPISODE])
    progress.forget("tt1375666", "")
    assert await sync() == 1  # only the episode lands
    assert progress.get("tt1375666", "") is None


async def test_sync_forget_of_whole_series_blocks_every_episode() -> None:
    sync, progress = make_sync([EPISODE])
    progress.forget("tt0898266")
    assert await sync() == 0
    assert progress.get("tt0898266", "tt0898266:2:5") is None


async def test_sync_applies_remote_activity_newer_than_the_forget() -> None:
    # Forgetting at T then watching further on another client at T+n is new
    # information, not a resurrection — it must land.
    entry = replace(MOVIE, paused_at=NOW + 100)
    sync, progress = make_sync([entry])
    progress.forget("tt1375666", "")  # tombstone at NOW
    assert await sync() == 1
    assert progress.get("tt1375666", "") is not None


async def test_sync_respects_reset_all() -> None:
    sync, progress = make_sync([MOVIE, EPISODE])
    assert await sync() == 2
    progress.reset_all()
    assert await sync() == 0
    assert progress.in_progress() == []


MOVIE_PLAY = TraktHistoryItem(
    media_type="movie",
    imdb_id="tt1375666",
    title="Inception",
    watched_at=NOW - 100,
)
EPISODE_PLAY = TraktHistoryItem(
    media_type="series",
    imdb_id="tt0898266",
    title="The Show",
    watched_at=NOW - 50,
    season=2,
    episode=5,
    episode_title="The Pilot",
)


async def test_history_marks_movie_watched() -> None:
    sync, progress = make_sync([], plays=[MOVIE_PLAY])
    assert await sync() == 1
    assert progress.is_watched("tt1375666", "")
    entry = progress.get("tt1375666", "")
    assert entry is not None
    assert entry.updated_at == MOVIE_PLAY.watched_at
    assert entry.name == "Inception"
    # A checkmark, not a resume bar: finished titles stay out of Continue
    # Watching.
    assert progress.in_progress() == []


async def test_history_marks_episode_watched_with_label() -> None:
    sync, progress = make_sync([], plays=[EPISODE_PLAY])
    assert await sync() == 1
    assert progress.is_watched("tt0898266", "tt0898266:2:5")
    entry = progress.get("tt0898266", "tt0898266:2:5")
    assert entry is not None
    assert entry.type == "series"
    assert entry.label == "S2E5 · The Pilot"


async def test_history_binge_repeats_apply_once() -> None:
    # Newest play first, like Trakt serves them; the older repeat is stale
    # against the row the first one wrote.
    older = replace(MOVIE_PLAY, watched_at=NOW - 900)
    sync, _progress = make_sync([], plays=[MOVIE_PLAY, older])
    assert await sync() == 1


async def test_history_never_clobbers_newer_local_progress() -> None:
    # Rewatching locally after the Trakt play: the local resume position is
    # fresher and must survive — no checkmark lands on top of it.
    sync, progress = make_sync([], plays=[MOVIE_PLAY])
    progress.record(
        media_id="tt1375666",
        video_id="",
        type="movie",
        name="Inception",
        poster="http://p/i.jpg",
        label="",
        position=1000.0,
        duration=148 * 60.0,
        updated_at=MOVIE_PLAY.watched_at + 500,
    )
    assert await sync() == 0
    local = progress.get("tt1375666", "")
    assert local is not None and not local.watched and local.position == 1000.0


async def test_history_respects_a_newer_local_forget() -> None:
    sync, progress = make_sync([], plays=[MOVIE_PLAY])
    progress.forget("tt1375666", "")  # tombstone at NOW > watched_at
    assert await sync() == 0
    assert not progress.is_watched("tt1375666", "")


async def test_history_keeps_the_poster_a_local_row_already_has() -> None:
    # The paused row (with its poster) came in earlier; finishing on another
    # client must flip it to watched without losing the artwork.
    sync, progress = make_sync([], plays=[replace(MOVIE_PLAY, watched_at=NOW)])
    progress.record(
        media_id="tt1375666",
        video_id="",
        type="movie",
        name="Inception",
        poster="http://p/inception.jpg",
        label="",
        position=1000.0,
        duration=148 * 60.0,
        updated_at=NOW - 500,
    )
    assert await sync() == 1
    entry = progress.get("tt1375666", "")
    assert entry is not None and entry.watched
    assert entry.poster == "http://p/inception.jpg"


async def test_playback_and_history_for_the_same_title_newest_wins() -> None:
    # Paused at NOW-100, finished at NOW-50: the checkmark is the newer fact.
    sync, progress = make_sync([MOVIE], plays=[replace(MOVIE_PLAY, watched_at=NOW - 50)])
    await sync()
    assert progress.is_watched("tt1375666", "")
