"""Use case: pull Trakt's paused-playback list into local watch progress.

One-way by design: Gravitas already pushes live via scrobbling, so this is
the other half — whatever another Trakt client (Stremio, Kodi, …) left
unfinished appears in Continue Watching here. A Trakt entry only lands when
it is NEWER than the local row for the same title, so syncing never clobbers
progress made in Gravitas itself.
"""

from __future__ import annotations

import logging

from gravitas.application.get_detail import GetDetail
from gravitas.application.trakt_account import TraktAccount
from gravitas.application.watch_progress import MIN_POSITION, WatchProgressRepository
from gravitas.domain.errors import GravitasError, TraktError
from gravitas.domain.models import TraktPlayback

_log = logging.getLogger(__name__)

# Newest entries only. Trakt keeps paused rows around for months; resurrecting
# a fifty-item backlog would bury the local Continue Watching row.
MAX_ENTRIES = 30


class TraktSync:
    def __init__(
        self,
        account: TraktAccount,
        progress: WatchProgressRepository,
        get_detail: GetDetail | None = None,
    ) -> None:
        self._account = account
        self._progress = progress
        # Trakt carries no artwork; the poster comes from the addons' own
        # /meta (best-effort — a missing poster still renders as a card).
        self._get_detail = get_detail

    async def __call__(self) -> int:
        """Apply newer Trakt playback entries locally; returns how many."""
        token = await self._account.ensure_token()
        if token is None or not self._account.client_id:
            raise TraktError("Trakt is not connected")
        entries = await self._account.api.playback(self._account.client_id, token)
        entries.sort(key=lambda e: e.paused_at, reverse=True)
        applied = 0
        for entry in entries[:MAX_ENTRIES]:
            if await self._apply(entry):
                applied += 1
        return applied

    async def _apply(self, entry: TraktPlayback) -> bool:
        if entry.runtime_minutes is None or entry.runtime_minutes <= 0:
            # Percent with no runtime cannot become a position in seconds.
            return False
        duration = float(entry.runtime_minutes * 60)
        position = max(0.0, min(100.0, entry.progress)) / 100.0 * duration
        if position < MIN_POSITION:
            return False
        video_id = (
            f"{entry.imdb_id}:{entry.season}:{entry.episode}"
            if entry.media_type == "series"
            else ""
        )
        local = self._progress.get(entry.imdb_id, video_id)
        if local is not None and local.updated_at >= entry.paused_at:
            # Local knowledge is at least as fresh — including "already
            # watched" and "resumed further in Gravitas".
            return False
        label = ""
        if entry.media_type == "series":
            label = f"S{entry.season}E{entry.episode}"
            if entry.episode_title:
                label += f" · {entry.episode_title}"
        self._progress.record(
            media_id=entry.imdb_id,
            video_id=video_id,
            type=entry.media_type,
            name=entry.title,
            poster=await self._poster(entry),
            label=label,
            position=position,
            duration=duration,
            updated_at=entry.paused_at,
        )
        return True

    async def _poster(self, entry: TraktPlayback) -> str | None:
        if self._get_detail is None:
            return None
        try:
            detail = await self._get_detail(entry.media_type, entry.imdb_id)
        except GravitasError as exc:
            _log.info("no poster for synced %s: %s", entry.imdb_id, exc)
            return None
        return detail.poster
