"""TraktApi adapter over httpx — device auth, token lifecycle and scrobbles.

Dumb HTTP only: no token storage, no refresh policy, no retry. That is
application territory (TraktAccount); this file just speaks the wire format
documented at https://trakt.docs.apiary.io/.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from gravitas.domain.errors import TraktError
from gravitas.domain.models import TraktAuth, TraktDeviceCode, TraktPlayback

_log = logging.getLogger(__name__)

_API = "https://api.trakt.tv"

# Device-token poll statuses that mean "keep waiting". 400 is the documented
# "pending" answer; 429 is "slow down", which the poll loop treats the same
# way (its fixed interval is already the server-requested one).
_PENDING_STATUSES = (400, 429)

_DENIED_REASONS = {
    404: "invalid device code",
    409: "code already approved",
    410: "the code expired — start again",
    418: "access was denied on trakt.tv",
}


def _headers(client_id: str, access_token: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _auth_from(payload: dict[str, Any]) -> TraktAuth:
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access or not isinstance(refresh, str):
        raise TraktError("unexpected Trakt token response")
    created = payload.get("created_at")
    expires_in = payload.get("expires_in")
    created_at = created if isinstance(created, int) else int(time.time())
    lifetime = expires_in if isinstance(expires_in, int) else 0
    return TraktAuth(
        access_token=access,
        refresh_token=refresh,
        expires_at=created_at + lifetime,
    )


def _epoch(iso: object) -> int:
    """Trakt timestamps are ISO 8601 UTC ("2026-07-17T02:24:30.000Z")."""
    if not isinstance(iso, str) or not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _playback_entry(raw: dict[str, Any]) -> TraktPlayback | None:
    """One /sync/playback item, or None if it isn't a movie/episode keyed by
    an imdb id (Trakt also carries entries our ids cannot address)."""
    progress = raw.get("progress")
    if isinstance(progress, bool) or not isinstance(progress, (int, float)):
        return None
    kind = raw.get("type")
    if kind == "movie":
        movie = raw.get("movie")
        if not isinstance(movie, dict):
            return None
        ids_raw = movie.get("ids")
        ids = ids_raw if isinstance(ids_raw, dict) else {}
        imdb = ids.get("imdb")
        if not isinstance(imdb, str) or not imdb.startswith("tt"):
            return None
        return TraktPlayback(
            media_type="movie",
            imdb_id=imdb,
            title=str(movie.get("title") or ""),
            progress=float(progress),
            paused_at=_epoch(raw.get("paused_at")),
            playback_id=_int_or_none(raw.get("id")) or 0,
            runtime_minutes=_int_or_none(movie.get("runtime")),
        )
    if kind == "episode":
        episode = raw.get("episode")
        show = raw.get("show")
        if not isinstance(episode, dict) or not isinstance(show, dict):
            return None
        ids_raw = show.get("ids")
        ids = ids_raw if isinstance(ids_raw, dict) else {}
        imdb = ids.get("imdb")
        season = _int_or_none(episode.get("season"))
        number = _int_or_none(episode.get("number"))
        if not isinstance(imdb, str) or not imdb.startswith("tt"):
            return None
        if season is None or number is None:
            return None
        return TraktPlayback(
            media_type="series",
            imdb_id=imdb,
            title=str(show.get("title") or ""),
            progress=float(progress),
            paused_at=_epoch(raw.get("paused_at")),
            playback_id=_int_or_none(raw.get("id")) or 0,
            season=season,
            episode=number,
            episode_title=str(episode.get("title")) if episode.get("title") else None,
            runtime_minutes=_int_or_none(episode.get("runtime")),
        )
    return None


class TraktClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _post(
        self, path: str, json: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        try:
            return await self._client.post(
                f"{_API}{path}", json=json, headers=headers, timeout=15.0
            )
        except httpx.HTTPError as exc:
            raise TraktError(f"Trakt request failed: {exc}") from exc

    async def device_code(self, client_id: str) -> TraktDeviceCode:
        resp = await self._post("/oauth/device/code", {"client_id": client_id}, _headers(client_id))
        if resp.status_code != 200:
            raise TraktError(
                f"Trakt rejected the device-code request (HTTP {resp.status_code}) — "
                "check the client ID",
                status=resp.status_code,
            )
        data = resp.json()
        try:
            return TraktDeviceCode(
                device_code=str(data["device_code"]),
                user_code=str(data["user_code"]),
                verification_url=str(data["verification_url"]),
                interval=max(1, int(data["interval"])),
                expires_in=int(data["expires_in"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TraktError("unexpected Trakt device-code response") from exc

    async def poll_device_token(
        self, client_id: str, client_secret: str, device_code: str
    ) -> TraktAuth | None:
        resp = await self._post(
            "/oauth/device/token",
            {"code": device_code, "client_id": client_id, "client_secret": client_secret},
            _headers(client_id),
        )
        if resp.status_code in _PENDING_STATUSES:
            return None
        if resp.status_code != 200:
            reason = _DENIED_REASONS.get(
                resp.status_code, f"device auth failed (HTTP {resp.status_code})"
            )
            raise TraktError(f"Trakt: {reason}", status=resp.status_code)
        return _auth_from(resp.json())

    async def refresh_token(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> TraktAuth:
        resp = await self._post(
            "/oauth/token",
            {
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "refresh_token",
            },
            _headers(client_id),
        )
        if resp.status_code != 200:
            raise TraktError(
                f"Trakt token refresh failed (HTTP {resp.status_code})",
                status=resp.status_code,
            )
        return _auth_from(resp.json())

    async def revoke(self, client_id: str, client_secret: str, access_token: str) -> None:
        resp = await self._post(
            "/oauth/revoke",
            {"token": access_token, "client_id": client_id, "client_secret": client_secret},
            _headers(client_id),
        )
        if resp.status_code != 200:
            raise TraktError(
                f"Trakt token revoke failed (HTTP {resp.status_code})",
                status=resp.status_code,
            )

    async def username(self, client_id: str, access_token: str) -> str:
        try:
            resp = await self._client.get(
                f"{_API}/users/me", headers=_headers(client_id, access_token), timeout=15.0
            )
        except httpx.HTTPError as exc:
            raise TraktError(f"Trakt request failed: {exc}") from exc
        if resp.status_code != 200:
            raise TraktError(
                f"Trakt profile lookup failed (HTTP {resp.status_code})",
                status=resp.status_code,
            )
        data = resp.json()
        name = data.get("username") if isinstance(data, dict) else None
        return name if isinstance(name, str) else ""

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
        payload: dict[str, Any] = {"progress": round(max(0.0, min(100.0, progress)), 2)}
        if season is not None and episode is not None:
            payload["show"] = {"ids": {"imdb": imdb_id}}
            payload["episode"] = {"season": season, "number": episode}
        else:
            payload["movie"] = {"ids": {"imdb": imdb_id}}
        resp = await self._post(f"/scrobble/{action}", payload, _headers(client_id, access_token))
        # 409 = a scrobble for this item was sent seconds ago; harmless echo
        # of a pause/resume flurry, not a failure worth surfacing.
        if resp.status_code not in (201, 409):
            raise TraktError(
                f"Trakt scrobble/{action} failed (HTTP {resp.status_code})",
                status=resp.status_code,
            )

    async def playback(self, client_id: str, access_token: str) -> list[TraktPlayback]:
        # extended=full carries the runtimes that turn Trakt's percent into a
        # local position in seconds.
        try:
            resp = await self._client.get(
                f"{_API}/sync/playback",
                params={"extended": "full"},
                headers=_headers(client_id, access_token),
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise TraktError(f"Trakt request failed: {exc}") from exc
        if resp.status_code != 200:
            raise TraktError(
                f"Trakt playback sync failed (HTTP {resp.status_code})",
                status=resp.status_code,
            )
        data = resp.json()
        if not isinstance(data, list):
            raise TraktError("unexpected Trakt playback response")
        entries: list[TraktPlayback] = []
        skipped = 0
        for raw in data:
            entry = _playback_entry(raw) if isinstance(raw, dict) else None
            if entry is not None:
                entries.append(entry)
            else:
                skipped += 1
        if skipped:
            _log.info("Trakt playback sync: skipped %d unaddressable entries", skipped)
        return entries

    async def remove_playback(self, client_id: str, access_token: str, playback_id: int) -> None:
        try:
            resp = await self._client.delete(
                f"{_API}/sync/playback/{playback_id}",
                headers=_headers(client_id, access_token),
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise TraktError(f"Trakt request failed: {exc}") from exc
        # 404 = already gone (another client beat us to it) — mission
        # accomplished either way.
        if resp.status_code not in (204, 404):
            raise TraktError(
                f"Trakt playback removal failed (HTTP {resp.status_code})",
                status=resp.status_code,
            )
