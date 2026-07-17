from gravitas.application.trakt_account import TraktAccount
from gravitas.domain.errors import TraktError
from gravitas.domain.models import TraktAuth, TraktDeviceCode

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
