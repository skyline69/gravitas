"""Trakt session state and scrobble policy.

Owns the mutable pieces (credentials, granted tokens) the rest of the app
reads, decides when a token is refreshed, and maps Gravitas's media context
onto Trakt's scrobble shape. The HTTP itself arrives through the TraktApi
port — this module never imports infrastructure.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from gravitas.domain.errors import TraktError
from gravitas.domain.models import MediaType, TraktAuth
from gravitas.domain.ports import TraktApi

_log = logging.getLogger(__name__)

# Refresh when the access token has less than this long to live. Trakt tokens
# last ~3 months, so this fires once in a blue moon — but a token that expires
# mid-film would silently drop the stop scrobble that marks it watched.
_REFRESH_MARGIN = 60 * 60 * 24


def _episode_ref(video_id: str) -> tuple[int, int] | None:
    """Cinemeta episode ids are "tt123:1:2" (show:season:episode)."""
    parts = video_id.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


class TraktAccount:
    def __init__(
        self,
        api: TraktApi,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._api = api
        self._clock = clock
        self.client_id: str | None = None
        self.client_secret: str | None = None
        self.auth: TraktAuth | None = None
        # User policy: whether local forgets / mark-as-watched mirror into
        # Trakt. Off silences only the outbound mirror — local state always
        # wins locally regardless (the sync respects forget tombstones).
        self.sync_forgets = True
        self.sync_watched = True
        # Fired after any token change this class makes on its own (a refresh
        # mid-scrobble), so the new tokens reach the settings file — otherwise
        # the next launch would come up with the revoked pair.
        self.on_auth_changed: Callable[[], None] | None = None

    @property
    def api(self) -> TraktApi:
        """The raw port, for flows that live outside this class (device auth
        is UI-shaped and runs in the presentation layer)."""
        return self._api

    @property
    def authenticated(self) -> bool:
        return self.auth is not None

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id) and bool(self.client_secret)

    async def ensure_token(self) -> str | None:
        """A currently-valid access token, refreshing if it is about to
        expire. None when not connected (scrobbling silently off)."""
        if self.auth is None or not self.client_id or not self.client_secret:
            return None
        if self.auth.expires_at - self._clock() < _REFRESH_MARGIN:
            await self._refresh()
            if self.auth is None:
                return None
        return self.auth.access_token

    async def _refresh(self) -> None:
        if self.auth is None or not self.client_id or not self.client_secret:
            return
        try:
            fresh = await self._api.refresh_token(
                self.client_id, self.client_secret, self.auth.refresh_token
            )
        except TraktError as exc:
            if exc.status == 401:
                # The refresh token itself was revoked — the session is dead
                # and every later call would fail the same way. Disconnect.
                _log.warning("Trakt session revoked; disconnecting: %s", exc)
                self.auth = None
                self._notify()
            else:
                _log.warning("Trakt token refresh failed: %s", exc)
            return
        # Keep the username: the token response does not carry it.
        self.auth = TraktAuth(
            access_token=fresh.access_token,
            refresh_token=fresh.refresh_token,
            expires_at=fresh.expires_at,
            username=self.auth.username,
        )
        self._notify()

    def _notify(self) -> None:
        if self.on_auth_changed is not None:
            self.on_auth_changed()

    async def scrobble(
        self,
        action: str,
        context: Mapping[str, object],
        position: float,
        duration: float,
    ) -> None:
        """Send one scrobble event, or quietly do nothing.

        Never raises: playback must not care whether Trakt is connected,
        reachable, or happy. `context` is the player's media context
        (mediaId/videoId/type)."""
        token = await self.ensure_token()
        if token is None or not self.client_id:
            return
        media_id = str(context.get("mediaId", ""))
        if not media_id.startswith("tt"):
            # Trakt is keyed by imdb id; addon-specific ids have no mapping.
            return
        season: int | None = None
        episode: int | None = None
        if context.get("type") == "series":
            ref = _episode_ref(str(context.get("videoId", "")))
            if ref is None:
                return
            season, episode = ref
        progress = (position / duration * 100.0) if duration > 0 else 0.0
        try:
            await self._api.scrobble(
                self.client_id,
                token,
                action,
                imdb_id=media_id,
                season=season,
                episode=episode,
                progress=progress,
            )
        except TraktError as exc:
            if exc.status == 401 and self.auth is not None:
                # Token died between checks; refresh once and retry.
                await self._refresh()
                retry = self.auth.access_token if self.auth is not None else None
                if retry is not None:
                    try:
                        await self._api.scrobble(
                            self.client_id,
                            retry,
                            action,
                            imdb_id=media_id,
                            season=season,
                            episode=episode,
                            progress=progress,
                        )
                        return
                    except TraktError as retry_exc:
                        exc = retry_exc
            _log.warning("Trakt scrobble/%s failed: %s", action, exc)

    async def mark_watched(self, context: Mapping[str, object]) -> None:
        """Mirror a local mark-as-watched into Trakt's history.

        Same contract as scrobble: never raises — the local mark already
        happened and stands regardless of the network. `context` is the same
        media context markWatched receives (mediaId/videoId/type); a series
        context without an episode ref means the whole show, which Trakt
        expands to every episode. Afterwards the matching paused-playback
        rows are dropped, mirroring how a local mark clears the resume bar.
        """
        if not self.sync_watched:
            return
        token = await self.ensure_token()
        if token is None or not self.client_id:
            return
        media_id = str(context.get("mediaId", ""))
        if not media_id.startswith("tt"):
            return
        media_type: MediaType = "series" if context.get("type") == "series" else "movie"
        video_id = str(context.get("videoId", ""))
        season: int | None = None
        episode: int | None = None
        if media_type == "series" and video_id:
            ref = _episode_ref(video_id)
            if ref is None:
                return
            season, episode = ref
        try:
            await self._with_token_retry(
                token,
                lambda tok: self._api.add_to_history(
                    self.client_id or "",
                    tok,
                    media_type=media_type,
                    imdb_id=media_id,
                    season=season,
                    episode=episode,
                ),
            )
        except TraktError as exc:
            _log.warning("Trakt history add for %s failed: %s", media_id, exc)
            return
        # A watched title has nothing to resume. movie -> its one row,
        # episode -> that row, whole show -> every row of the media. Straight
        # to the ungated helper: this cleanup belongs to the watched mirror,
        # not to the sync_forgets policy.
        if media_type == "movie":
            await self._drop_paused_rows(media_id, "")
        elif video_id:
            await self._drop_paused_rows(media_id, video_id)
        else:
            await self._drop_paused_rows(media_id, None)

    async def _with_token_retry(self, token: str, call: Callable[[str], Awaitable[None]]) -> None:
        """Run an authed call; on 401 refresh once and retry — the token can
        die between ensure_token() and the request."""
        try:
            await call(token)
        except TraktError as exc:
            if exc.status != 401 or self.auth is None:
                raise
            await self._refresh()
            retry = self.auth.access_token if self.auth is not None else None
            if retry is None:
                raise
            await call(retry)

    async def remove_playback(self, media_id: str, video_id: str | None) -> None:
        """Drop the Trakt paused-playback rows matching a locally-forgotten
        title. No-op while sync_forgets is off — the local forget still
        stands, because the pull sync honours the forget tombstone.

        `video_id` "" targets the movie row, "ttX:S:E" one episode, None every
        row of the media (a whole-series forget). Never raises — the local
        forget already happened and must stand regardless of the network."""
        if not self.sync_forgets:
            return
        await self._drop_paused_rows(media_id, video_id)

    async def _drop_paused_rows(self, media_id: str, video_id: str | None) -> None:
        token = await self.ensure_token()
        if token is None or not self.client_id:
            return
        if not media_id.startswith("tt"):
            return
        episode_ref = _episode_ref(video_id) if video_id else None
        try:
            entries = await self._api.playback(self.client_id, token)
            for entry in entries:
                if entry.imdb_id != media_id or not entry.playback_id:
                    continue
                if video_id is not None:
                    if video_id == "" and entry.media_type != "movie":
                        continue
                    if video_id != "" and (entry.season, entry.episode) != episode_ref:
                        continue
                await self._api.remove_playback(self.client_id, token, entry.playback_id)
        except TraktError as exc:
            _log.warning("Trakt playback removal for %s failed: %s", media_id, exc)

    async def clear_playback(self) -> None:
        """Reset-all's Trakt half: drop every paused-playback row. Same
        never-raises contract (and sync_forgets gate) as remove_playback."""
        if not self.sync_forgets:
            return
        token = await self.ensure_token()
        if token is None or not self.client_id:
            return
        try:
            for entry in await self._api.playback(self.client_id, token):
                if entry.playback_id:
                    await self._api.remove_playback(self.client_id, token, entry.playback_id)
        except TraktError as exc:
            _log.warning("Trakt playback clear failed: %s", exc)
