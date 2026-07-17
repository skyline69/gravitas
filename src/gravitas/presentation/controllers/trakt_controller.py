"""QObject bridge: Trakt device auth for Settings, and the scrobble sink.

The device flow is UI-shaped — show a code, wait for the user to type it into
trakt.tv on any device, poll until granted — so it lives here rather than in
the application layer, which keeps only the session/token policy
(TraktAccount).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.trakt_account import TraktAccount
from gravitas.application.trakt_sync import TraktSync
from gravitas.domain.errors import TraktError
from gravitas.domain.models import TraktAuth


def _open_in_browser(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class TraktController(QObject):
    errorOccurred = Signal(str)
    traktChanged = Signal()
    # Fired after a pull from Trakt applied N entries; main.py routes it into
    # the progress-refresh fan-out so bars and Continue Watching update.
    syncCompleted = Signal(int)

    def __init__(
        self,
        account: TraktAccount,
        persist: Callable[[], None] | None = None,
        sync: TraktSync | None = None,
        # Test seams: the poll loop sleeps with `sleep` (tests inject a no-op
        # to poll instantly); `open_url` launches the system browser.
        sleep: Callable[[float], Awaitable[object]] | None = None,
        open_url: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._account = account
        self._persist = persist
        self._sync = sync
        self._syncing = False
        self._sleep: Callable[[float], Awaitable[object]] = (
            sleep if sleep is not None else asyncio.sleep
        )
        self._open_url: Callable[[str], None] = (
            open_url if open_url is not None else _open_in_browser
        )
        self._polling = False
        self._cancelled = False
        self._user_code = ""
        self._verification_url = ""
        # A mid-scrobble refresh writes new tokens; they must hit disk or the
        # next launch restores the revoked pair.
        account.on_auth_changed = self._on_account_changed

    def _on_account_changed(self) -> None:
        if self._persist is not None:
            self._persist()
        self.traktChanged.emit()

    # --- state the Settings card binds to ---

    @Property(bool, notify=traktChanged)
    def authenticated(self) -> bool:
        return self._account.authenticated

    @Property(str, notify=traktChanged)
    def username(self) -> str:
        return self._account.auth.username if self._account.auth is not None else ""

    @Property(bool, notify=traktChanged)
    def authInProgress(self) -> bool:
        return self._polling

    @Property(str, notify=traktChanged)
    def userCode(self) -> str:
        return self._user_code

    @Property(str, notify=traktChanged)
    def verificationUrl(self) -> str:
        return self._verification_url

    @Property(bool, notify=traktChanged)
    def syncing(self) -> bool:
        return self._syncing

    # --- playback sync (Trakt -> local Continue Watching) ---

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def syncNow(self) -> None:
        """Manual pull from the Settings button; failures surface as toasts."""
        try:
            applied = await self._run_sync()
        except TraktError as exc:
            self.errorOccurred.emit(str(exc))
            return
        self.syncCompleted.emit(applied)

    async def sync_quietly(self) -> None:
        """Startup pull: best-effort, log-only — a dead network must not toast
        over the first launch frame."""
        try:
            applied = await self._run_sync()
        except TraktError:
            return
        if applied:
            self.syncCompleted.emit(applied)

    async def _run_sync(self) -> int:
        if self._sync is None:
            raise TraktError("Trakt sync is not wired up")
        if self._syncing:
            return 0
        self._syncing = True
        self.traktChanged.emit()
        try:
            return await self._sync()
        finally:
            self._syncing = False
            self.traktChanged.emit()

    # --- device auth ---

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def startAuth(self) -> None:
        if self._polling:
            return
        if not self._account.has_credentials:
            self.errorOccurred.emit(
                "This build has no Trakt app credentials — set GRAVITAS_TRAKT_CLIENT_ID "
                "and GRAVITAS_TRAKT_CLIENT_SECRET (see infrastructure/trakt/app_credentials.py)"
            )
            return
        client_id = self._account.client_id or ""
        client_secret = self._account.client_secret or ""
        try:
            code = await self._account.api.device_code(client_id)
        except TraktError as exc:
            self.errorOccurred.emit(str(exc))
            return
        self._polling = True
        self._cancelled = False
        self._user_code = code.user_code
        self._verification_url = code.verification_url
        self.traktChanged.emit()
        # Stremio-style: hand the browser trakt.tv/activate with the code
        # already filled in — the user just signs in and clicks Yes. The code
        # stays visible in Settings as a fallback (headless browser, other
        # device).
        self._open_url(f"{code.verification_url}/{code.user_code}")
        try:
            waited = 0.0
            while not self._cancelled and waited < code.expires_in:
                await self._sleep(code.interval)
                waited += code.interval
                try:
                    auth = await self._account.api.poll_device_token(
                        client_id, client_secret, code.device_code
                    )
                except TraktError as exc:
                    self.errorOccurred.emit(str(exc))
                    return
                if auth is None:
                    continue
                await self._grant(auth)
                return
            if not self._cancelled:
                self.errorOccurred.emit("Trakt code expired — try connecting again")
        finally:
            self._polling = False
            self._user_code = ""
            self._verification_url = ""
            self.traktChanged.emit()

    async def _grant(self, auth: TraktAuth) -> None:
        username = ""
        # Failure is cosmetic only; the grant still stands.
        with contextlib.suppress(TraktError):
            username = await self._account.api.username(
                self._account.client_id or "", auth.access_token
            )
        self._account.auth = TraktAuth(
            access_token=auth.access_token,
            refresh_token=auth.refresh_token,
            expires_at=auth.expires_at,
            username=username,
        )
        self._on_account_changed()

    @Slot()
    def cancelAuth(self) -> None:
        self._cancelled = True

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def logout(self) -> None:
        auth = self._account.auth
        self._account.auth = None
        self._on_account_changed()
        # Best-effort revoke AFTER local state is cleared: the user asked to
        # be logged out, and a network hiccup must not veto that.
        if auth is not None and self._account.has_credentials:
            with contextlib.suppress(TraktError):
                await self._account.api.revoke(
                    self._account.client_id or "",
                    self._account.client_secret or "",
                    auth.access_token,
                )

    # --- scrobble sink (wired to PlayerController.scrobbleEvent) ---

    @asyncSlot(str, "QVariantMap", float, float)  # type: ignore[untyped-decorator]
    async def onScrobbleEvent(
        self, action: str, context: dict[str, object], position: float, duration: float
    ) -> None:
        await self._account.scrobble(action, context, position, duration)

    # --- forget mirrors (wired to ProgressController's forget signals) ---
    # A local forget must also drop Trakt's paused row, or the next sync
    # resurrects exactly what the user just deleted.

    @asyncSlot(str, str)  # type: ignore[untyped-decorator]
    async def onProgressForgotten(self, media_id: str, video_id: str) -> None:
        await self._account.remove_playback(media_id, video_id)

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def onMediaForgotten(self, media_id: str) -> None:
        await self._account.remove_playback(media_id, None)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def onAllProgressReset(self) -> None:
        await self._account.clear_playback()
