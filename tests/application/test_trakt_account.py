from gravitas.application.trakt_account import TraktAccount
from gravitas.domain.errors import TraktError
from gravitas.domain.models import (
    MediaType,
    TraktAuth,
    TraktDeviceCode,
    TraktHistoryItem,
    TraktListItem,
    TraktPlayback,
)

NOW = 1_700_000_000
FRESH = TraktAuth("ACCESS", "REFRESH", NOW + 90 * 86_400, "sky")
STALE = TraktAuth("OLD", "REFRESH", NOW + 60, "sky")  # about to expire

MOVIE_CTX = {"mediaId": "tt1375666", "videoId": "", "type": "movie"}
EPISODE_CTX = {"mediaId": "tt0898266", "videoId": "tt0898266:2:5", "type": "series"}


class FakeApi:
    def __init__(self) -> None:
        self.scrobbles: list[tuple[str, str, str, int | None, int | None, float]] = []
        self.refreshes = 0
        self.fail_statuses: list[int] = []  # consumed per scrobble call
        self.refresh_error: TraktError | None = None
        self.playback_entries: list[TraktPlayback] = []
        self.playback_error: TraktError | None = None
        self.removed: list[int] = []
        self.history_adds: list[tuple[str, str, str, int | None, int | None]] = []
        self.history_fail_statuses: list[int] = []  # consumed per add call

    async def device_code(self, client_id: str) -> TraktDeviceCode:
        raise NotImplementedError

    async def poll_device_token(
        self, client_id: str, client_secret: str, device_code: str
    ) -> TraktAuth | None:
        raise NotImplementedError

    async def refresh_token(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> TraktAuth:
        if self.refresh_error is not None:
            raise self.refresh_error
        self.refreshes += 1
        return TraktAuth("NEW", "NEWREFRESH", NOW + 90 * 86_400, "")

    async def revoke(self, client_id: str, client_secret: str, access_token: str) -> None:
        pass

    async def username(self, client_id: str, access_token: str) -> str:
        return "sky"

    async def scrobble(
        self,
        client_id: str,
        access_token: str,
        action: str,
        *,
        imdb_id: str,
        season: int | None,
        episode: int | None,
        progress: float,
    ) -> None:
        if self.fail_statuses:
            raise TraktError("nope", status=self.fail_statuses.pop(0))
        self.scrobbles.append((access_token, action, imdb_id, season, episode, progress))

    async def playback(self, client_id: str, access_token: str) -> list[TraktPlayback]:
        if self.playback_error is not None:
            raise self.playback_error
        return list(self.playback_entries)

    async def remove_playback(self, client_id: str, access_token: str, playback_id: int) -> None:
        self.removed.append(playback_id)

    async def recommendations(
        self, client_id: str, access_token: str, media_type: MediaType, limit: int
    ) -> list[TraktListItem]:
        raise NotImplementedError

    async def history(
        self, client_id: str, access_token: str, limit: int
    ) -> list[TraktHistoryItem]:
        raise NotImplementedError

    async def add_to_history(
        self,
        client_id: str,
        access_token: str,
        *,
        media_type: MediaType,
        imdb_id: str,
        season: int | None,
        episode: int | None,
    ) -> None:
        if self.history_fail_statuses:
            raise TraktError("nope", status=self.history_fail_statuses.pop(0))
        self.history_adds.append((access_token, media_type, imdb_id, season, episode))


def make_account(auth: TraktAuth | None = FRESH) -> tuple[TraktAccount, FakeApi]:
    api = FakeApi()
    account = TraktAccount(api, clock=lambda: NOW)
    account.client_id = "CID"
    account.client_secret = "SEC"
    account.auth = auth
    return account, api


async def test_ensure_token_none_when_disconnected() -> None:
    account, _ = make_account(auth=None)
    assert await account.ensure_token() is None


async def test_ensure_token_none_without_credentials() -> None:
    account, _ = make_account()
    account.client_secret = None
    assert await account.ensure_token() is None


async def test_ensure_token_passes_fresh_token_through() -> None:
    account, api = make_account()
    assert await account.ensure_token() == "ACCESS"
    assert api.refreshes == 0


async def test_ensure_token_refreshes_expiring_and_notifies() -> None:
    account, api = make_account(auth=STALE)
    changed: list[None] = []
    account.on_auth_changed = lambda: changed.append(None)
    assert await account.ensure_token() == "NEW"
    assert api.refreshes == 1
    assert changed == [None]
    # Username survives the refresh (the token response has none).
    assert account.auth is not None and account.auth.username == "sky"


async def test_refresh_revoked_session_disconnects() -> None:
    account, api = make_account(auth=STALE)
    api.refresh_error = TraktError("revoked", status=401)
    assert await account.ensure_token() is None
    assert account.auth is None


async def test_refresh_transient_failure_keeps_session() -> None:
    account, api = make_account(auth=STALE)
    api.refresh_error = TraktError("down", status=503)
    # The stale token is still returned — it may well still work, and
    # dropping the session over a blip would sign the user out.
    assert await account.ensure_token() == "OLD"
    assert account.auth is not None


async def test_scrobble_movie_maps_progress() -> None:
    account, api = make_account()
    await account.scrobble("start", MOVIE_CTX, 300.0, 600.0)
    assert api.scrobbles == [("ACCESS", "start", "tt1375666", None, None, 50.0)]


async def test_scrobble_episode_parses_video_id() -> None:
    account, api = make_account()
    await account.scrobble("stop", EPISODE_CTX, 570.0, 600.0)
    assert api.scrobbles == [("ACCESS", "stop", "tt0898266", 2, 5, 95.0)]


async def test_scrobble_noop_when_disconnected() -> None:
    account, api = make_account(auth=None)
    await account.scrobble("start", MOVIE_CTX, 10.0, 100.0)
    assert api.scrobbles == []


async def test_scrobble_skips_non_imdb_ids() -> None:
    account, api = make_account()
    await account.scrobble("start", {**MOVIE_CTX, "mediaId": "kitsu:1"}, 10.0, 100.0)
    assert api.scrobbles == []


async def test_scrobble_skips_unparseable_episode_ids() -> None:
    account, api = make_account()
    await account.scrobble("start", {**EPISODE_CTX, "videoId": "weird"}, 10.0, 100.0)
    assert api.scrobbles == []


async def test_scrobble_zero_duration_sends_zero_progress() -> None:
    account, api = make_account()
    await account.scrobble("start", MOVIE_CTX, 10.0, 0.0)
    assert api.scrobbles[0][5] == 0.0


async def test_scrobble_401_refreshes_and_retries_once() -> None:
    account, api = make_account()
    api.fail_statuses = [401]
    await account.scrobble("start", MOVIE_CTX, 300.0, 600.0)
    assert api.refreshes == 1
    assert api.scrobbles == [("NEW", "start", "tt1375666", None, None, 50.0)]


async def test_scrobble_other_errors_swallowed() -> None:
    account, api = make_account()
    api.fail_statuses = [503]
    await account.scrobble("start", MOVIE_CTX, 300.0, 600.0)  # must not raise
    assert api.scrobbles == []


_PLAYBACK_ROWS = [
    TraktPlayback(
        media_type="movie",
        imdb_id="tt1375666",
        title="Inception",
        progress=25.0,
        paused_at=NOW,
        playback_id=13,
    ),
    TraktPlayback(
        media_type="series",
        imdb_id="tt0898266",
        title="The Show",
        progress=21.0,
        paused_at=NOW,
        playback_id=37,
        season=1,
        episode=1,
    ),
    TraktPlayback(
        media_type="series",
        imdb_id="tt0898266",
        title="The Show",
        progress=50.0,
        paused_at=NOW,
        playback_id=38,
        season=1,
        episode=2,
    ),
]


async def test_remove_playback_targets_the_movie_row() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.remove_playback("tt1375666", "")
    assert api.removed == [13]


async def test_remove_playback_targets_one_episode() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.remove_playback("tt0898266", "tt0898266:1:1")
    assert api.removed == [37]


async def test_remove_playback_whole_media_drops_every_row() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.remove_playback("tt0898266", None)
    assert api.removed == [37, 38]


async def test_remove_playback_noop_when_disconnected() -> None:
    account, api = make_account(auth=None)
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.remove_playback("tt1375666", "")
    assert api.removed == []


async def test_remove_playback_skips_non_imdb_ids() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.remove_playback("kitsu:1", None)
    assert api.removed == []


async def test_remove_playback_errors_swallowed() -> None:
    account, api = make_account()
    api.playback_error = TraktError("down", status=503)
    await account.remove_playback("tt1375666", "")  # must not raise
    assert api.removed == []


async def test_mark_watched_movie_adds_history_and_drops_paused_row() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.mark_watched(MOVIE_CTX)
    assert api.history_adds == [("ACCESS", "movie", "tt1375666", None, None)]
    assert api.removed == [13]


async def test_mark_watched_episode_parses_video_id() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.mark_watched({**EPISODE_CTX, "videoId": "tt0898266:1:1"})
    assert api.history_adds == [("ACCESS", "series", "tt0898266", 1, 1)]
    assert api.removed == [37]


async def test_mark_watched_whole_show_adds_show_and_drops_every_row() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.mark_watched({"mediaId": "tt0898266", "videoId": "", "type": "series"})
    assert api.history_adds == [("ACCESS", "series", "tt0898266", None, None)]
    assert api.removed == [37, 38]


async def test_mark_watched_noop_when_disconnected() -> None:
    account, api = make_account(auth=None)
    await account.mark_watched(MOVIE_CTX)
    assert api.history_adds == []


async def test_mark_watched_skips_non_imdb_ids() -> None:
    account, api = make_account()
    await account.mark_watched({**MOVIE_CTX, "mediaId": "kitsu:1"})
    assert api.history_adds == []


async def test_mark_watched_skips_unparseable_episode_ids() -> None:
    account, api = make_account()
    await account.mark_watched({**EPISODE_CTX, "videoId": "weird"})
    assert api.history_adds == []


async def test_mark_watched_401_refreshes_and_retries_once() -> None:
    account, api = make_account()
    api.history_fail_statuses = [401]
    await account.mark_watched(MOVIE_CTX)
    assert api.refreshes == 1
    assert api.history_adds == [("NEW", "movie", "tt1375666", None, None)]


async def test_mark_watched_other_errors_swallowed_and_skip_removal() -> None:
    account, api = make_account()
    api.history_fail_statuses = [503]
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.mark_watched(MOVIE_CTX)  # must not raise
    assert api.history_adds == []
    # The mark never reached Trakt, so its paused row must survive.
    assert api.removed == []


async def test_clear_playback_drops_everything() -> None:
    account, api = make_account()
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.clear_playback()
    assert api.removed == [13, 37, 38]


async def test_sync_forgets_off_silences_playback_removal() -> None:
    account, api = make_account()
    account.sync_forgets = False
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.remove_playback("tt1375666", "")
    await account.clear_playback()
    assert api.removed == []


async def test_sync_watched_off_silences_history_add() -> None:
    account, api = make_account()
    account.sync_watched = False
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.mark_watched(MOVIE_CTX)
    assert api.history_adds == []
    assert api.removed == []


async def test_mark_watched_drops_paused_rows_even_with_sync_forgets_off() -> None:
    # The paused-row cleanup belongs to the watched mirror, not the forget
    # mirror — a watched title has nothing left to resume anywhere.
    account, api = make_account()
    account.sync_forgets = False
    api.playback_entries = list(_PLAYBACK_ROWS)
    await account.mark_watched(MOVIE_CTX)
    assert api.history_adds == [("ACCESS", "movie", "tt1375666", None, None)]
    assert api.removed == [13]
