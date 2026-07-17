from gravitas.application.trakt_account import TraktAccount
from gravitas.domain.errors import TraktError
from gravitas.domain.models import TraktAuth, TraktDeviceCode

CODE = TraktDeviceCode(
    device_code="DEV",
    user_code="1A2B3C4D",
    verification_url="https://trakt.tv/activate",
    interval=1,
    expires_in=10,
)

AUTH = TraktAuth("ACCESS", "REFRESH", 9_999_999_999)


class FakeApi:
    def __init__(self, polls_until_grant: int = 1, deny: bool = False) -> None:
        self.polls = 0
        self.polls_until_grant = polls_until_grant
        self.deny = deny
        self.revoked: list[str] = []

    async def device_code(self, client_id: str) -> TraktDeviceCode:
        return CODE

    async def poll_device_token(
        self, client_id: str, client_secret: str, device_code: str
    ) -> TraktAuth | None:
        self.polls += 1
        if self.deny:
            raise TraktError("Trakt: access was denied on trakt.tv", status=418)
        if self.polls >= self.polls_until_grant:
            return AUTH
        return None

    async def refresh_token(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> TraktAuth:
        raise NotImplementedError

    async def revoke(self, client_id: str, client_secret: str, access_token: str) -> None:
        self.revoked.append(access_token)

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
        pass


async def _noop_sleep(_seconds: float) -> None:
    return None


def make_controller(api: FakeApi, with_creds: bool = True):  # type: ignore[no-untyped-def]
    from gravitas.presentation.controllers.trakt_controller import TraktController

    account = TraktAccount(api, clock=lambda: 0)
    if with_creds:
        account.client_id = "CID"
        account.client_secret = "SEC"
    persists: list[None] = []
    opened: list[str] = []
    controller = TraktController(
        account, lambda: persists.append(None), sleep=_noop_sleep, open_url=opened.append
    )
    return controller, account, persists, opened


async def test_start_auth_grants_and_persists(qapp: object) -> None:
    api = FakeApi(polls_until_grant=3)
    controller, account, persists, opened = make_controller(api)

    await controller.startAuth()

    assert account.auth is not None
    assert account.auth.access_token == "ACCESS"
    assert account.auth.username == "sky"
    assert controller.authenticated is True
    assert controller.username == "sky"
    assert controller.authInProgress is False
    assert api.polls == 3
    assert persists  # tokens reached the settings file
    # Stremio-style: the browser opened on trakt.tv with the code pre-filled.
    assert opened == ["https://trakt.tv/activate/1A2B3C4D"]


async def test_start_auth_without_credentials_errors(qapp: object) -> None:
    controller, account, _, opened = make_controller(FakeApi(), with_creds=False)
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.startAuth()
    assert errors and "credentials" in errors[0]
    assert account.auth is None
    assert opened == []  # no browser without a device code


async def test_start_auth_denied_surfaces_error(qapp: object) -> None:
    controller, account, _, _opened = make_controller(FakeApi(deny=True))
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.startAuth()
    assert errors and "denied" in errors[0]
    assert account.auth is None
    assert controller.authInProgress is False


async def test_start_auth_expiry_reports_and_resets(qapp: object) -> None:
    # Never grants; interval 1 x expires_in 10 → the loop runs out of window.
    controller, account, _, _opened = make_controller(FakeApi(polls_until_grant=99))
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.startAuth()
    assert errors and "expired" in errors[0]
    assert account.auth is None
    assert controller.userCode == ""


async def test_code_exposed_while_polling(qapp: object) -> None:
    api = FakeApi(polls_until_grant=2)
    controller, _account, _, _opened = make_controller(api)
    seen: list[tuple[str, str, bool]] = []
    controller.traktChanged.connect(
        lambda: seen.append(
            (controller.userCode, controller.verificationUrl, controller.authInProgress)
        )
    )
    await controller.startAuth()
    # First change: polling began with the code visible.
    assert ("1A2B3C4D", "https://trakt.tv/activate", True) in seen
    # Cleared once done.
    assert controller.userCode == ""


async def test_cancel_stops_polling(qapp: object) -> None:
    api = FakeApi(polls_until_grant=99)
    controller, account, _, _opened = make_controller(api)

    # Cancel as soon as the code is up (first traktChanged with polling on).
    def _cancel_once() -> None:
        if controller.authInProgress:
            controller.cancelAuth()

    controller.traktChanged.connect(_cancel_once)
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.startAuth()
    assert account.auth is None
    assert errors == []  # a user cancel is not an error
    assert api.polls <= 1


async def test_logout_clears_and_revokes(qapp: object) -> None:
    api = FakeApi()
    controller, account, persists, _opened = make_controller(api)
    account.auth = AUTH
    await controller.logout()
    assert account.auth is None
    assert controller.authenticated is False
    assert api.revoked == ["ACCESS"]
    assert persists


class FakeSync:
    def __init__(self, applied: int = 3, fail: bool = False) -> None:
        self.applied = applied
        self.fail = fail
        self.calls = 0

    async def __call__(self) -> int:
        self.calls += 1
        if self.fail:
            raise TraktError("Trakt playback sync failed (HTTP 503)", status=503)
        return self.applied


async def test_sync_now_emits_completed(qapp: object) -> None:
    from gravitas.presentation.controllers.trakt_controller import TraktController

    account = TraktAccount(FakeApi(), clock=lambda: 0)
    sync = FakeSync(applied=3)
    controller = TraktController(account, None, sync, sleep=_noop_sleep)  # type: ignore[arg-type]
    done: list[int] = []
    controller.syncCompleted.connect(done.append)
    await controller.syncNow()
    assert done == [3]
    assert sync.calls == 1
    assert controller.syncing is False


async def test_sync_now_surfaces_errors(qapp: object) -> None:
    from gravitas.presentation.controllers.trakt_controller import TraktController

    account = TraktAccount(FakeApi(), clock=lambda: 0)
    controller = TraktController(account, None, FakeSync(fail=True), sleep=_noop_sleep)  # type: ignore[arg-type]
    errors: list[str] = []
    done: list[int] = []
    controller.errorOccurred.connect(errors.append)
    controller.syncCompleted.connect(done.append)
    await controller.syncNow()
    assert errors and "sync failed" in errors[0]
    assert done == []
    assert controller.syncing is False


async def test_sync_quietly_swallows_errors_and_emits_on_success(qapp: object) -> None:
    from gravitas.presentation.controllers.trakt_controller import TraktController

    account = TraktAccount(FakeApi(), clock=lambda: 0)
    controller = TraktController(account, None, FakeSync(fail=True), sleep=_noop_sleep)  # type: ignore[arg-type]
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.sync_quietly()
    assert errors == []  # startup must not toast over the first frame

    ok = TraktController(account, None, FakeSync(applied=2), sleep=_noop_sleep)  # type: ignore[arg-type]
    done: list[int] = []
    ok.syncCompleted.connect(done.append)
    await ok.sync_quietly()
    assert done == [2]


async def test_sync_quietly_zero_applied_stays_silent(qapp: object) -> None:
    from gravitas.presentation.controllers.trakt_controller import TraktController

    account = TraktAccount(FakeApi(), clock=lambda: 0)
    controller = TraktController(account, None, FakeSync(applied=0), sleep=_noop_sleep)  # type: ignore[arg-type]
    done: list[int] = []
    controller.syncCompleted.connect(done.append)
    await controller.sync_quietly()
    assert done == []  # nothing pulled, nothing to announce


async def test_forget_slots_forward_to_account(qapp: object) -> None:
    from gravitas.presentation.controllers.trakt_controller import TraktController

    account = TraktAccount(FakeApi(), clock=lambda: 0)
    controller = TraktController(account, None, sleep=_noop_sleep)
    calls: list[tuple[str, str | None]] = []
    cleared: list[None] = []

    async def fake_remove(media_id: str, video_id: str | None) -> None:
        calls.append((media_id, video_id))

    async def fake_clear() -> None:
        cleared.append(None)

    account.remove_playback = fake_remove  # type: ignore[method-assign]
    account.clear_playback = fake_clear  # type: ignore[method-assign]

    await controller.onProgressForgotten("tt1", "")
    await controller.onMediaForgotten("tt9")
    await controller.onAllProgressReset()

    assert calls == [("tt1", ""), ("tt9", None)]
    assert cleared == [None]


async def test_scrobble_event_forwards_to_account(qapp: object) -> None:
    api = FakeApi()
    controller, account, _, _opened = make_controller(api)
    account.auth = AUTH
    calls: list[tuple[str, float, float]] = []

    async def fake_scrobble(action: str, context: object, position: float, duration: float) -> None:
        calls.append((action, position, duration))

    account.scrobble = fake_scrobble  # type: ignore[method-assign]
    await controller.onScrobbleEvent("pause", {"mediaId": "tt1"}, 30.0, 60.0)
    assert calls == [("pause", 30.0, 60.0)]
