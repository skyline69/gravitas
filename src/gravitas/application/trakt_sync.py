"""Use case: pull Trakt's paused playback AND watch history into local state.

One-way by design: Gravitas already pushes live via scrobbling (and mirrors
marks/forgets), so this is the other half — whatever another Trakt client
(Stremio, Kodi, …) left unfinished appears in Continue Watching here, and
whatever it finished shows its checkmark. An entry only lands when it is
NEWER than both the local row and any forget tombstone for the same title,
so syncing never clobbers or resurrects anything done in Gravitas itself.
"""

from __future__ import annotations

import logging

from gravitas.application.get_detail import GetDetail
from gravitas.application.trakt_account import TraktAccount
from gravitas.application.watch_progress import MIN_POSITION, WatchProgressRepository
from gravitas.domain.errors import GravitasError, TraktError
from gravitas.domain.models import TraktHistoryItem, TraktPlayback

_log = logging.getLogger(__name__)

# Newest entries only. Trakt keeps paused rows around for months; resurrecting
# a fifty-item backlog would bury the local Continue Watching row.
MAX_ENTRIES = 30
# History plays to examine per sync. Plays, not titles — a binged season is
# ~10 of these. Watched marks are cheap rows (a checkmark, pruned at 2000),
# so this is deliberately deeper than MAX_ENTRIES.
MAX_HISTORY_PLAYS = 100


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
        """Apply newer Trakt playback and history entries locally; returns
        how many landed (resume positions + watched marks combined)."""
        token = await self._account.ensure_token()
        if token is None or not self._account.client_id:
            raise TraktError("Trakt is not connected")
        entries = await self._account.api.playback(self._account.client_id, token)
        entries.sort(key=lambda e: e.paused_at, reverse=True)
        applied = 0
        for entry in entries[:MAX_ENTRIES]:
            if await self._apply(entry):
                applied += 1
        # Then the finished titles. After playback on purpose: both phases
        # only ever apply entries newer than local state, so between two
        # Trakt rows for the same title the newer one wins regardless of
        # order — this order just makes the common case (finished later than
        # paused) a single write.
        plays = await self._account.api.history(self._account.client_id, token, MAX_HISTORY_PLAYS)
        for play in plays:
            if self._apply_history(play):
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
        if entry.paused_at <= self._progress.forgotten_at(entry.imdb_id, video_id):
            # The user forgot this locally after Trakt last saw it. A deleted
            # row looks identical to "never started", so without this check
            # every sync would resurrect it — even (especially) when the
            # forget was never mirrored to Trakt. Newer remote activity still
            # wins: watching further on another client is new information,
            # not a resurrection.
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

    def _apply_history(self, play: TraktHistoryItem) -> bool:
        """One history play into a local watched mark. Network-free: a
        checkmark needs no runtime and no poster (watched rows never render
        a card of their own — they tint posters other surfaces already
        loaded)."""
        video_id = (
            f"{play.imdb_id}:{play.season}:{play.episode}" if play.media_type == "series" else ""
        )
        local = self._progress.get(play.imdb_id, video_id)
        if local is not None and local.updated_at >= play.watched_at:
            # Local knowledge is at least as fresh — including "already
            # watched" (a binge lists the same episode per play; the first,
            # newest play lands and the rest stop here).
            return False
        if play.watched_at <= self._progress.forgotten_at(play.imdb_id, video_id):
            # Forgotten locally after Trakt saw the play — same rule as the
            # playback phase: a forget is authoritative until genuinely
            # newer remote activity appears.
            return False
        label = ""
        if play.media_type == "series":
            label = f"S{play.season}E{play.episode}"
            if play.episode_title:
                label += f" · {play.episode_title}"
        self._progress.mark_watched(
            media_id=play.imdb_id,
            video_id=video_id,
            type=play.media_type,
            name=play.title,
            # Keep whatever poster the row already carries; history has none.
            poster=local.poster if local is not None else None,
            label=label,
            updated_at=play.watched_at,
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
