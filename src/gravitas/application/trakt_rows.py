"""Use case: build the personalized Home rows Trakt can serve.

Three rows — recommended movies, recommended series, recently watched —
fetched fresh from Trakt on each call. Trakt carries no artwork, so every
item's poster comes from the addons' own /meta (best-effort and concurrent;
AddonClient caches meta 24h, so repeat calls are cheap). Each row is
fault-isolated like addon catalogs: one dead endpoint drops its row, not
the others.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from gravitas.application.get_detail import GetDetail
from gravitas.application.trakt_account import TraktAccount
from gravitas.domain.errors import GravitasError, TraktError
from gravitas.domain.models import MediaItem, TraktListItem

_log = logging.getLogger(__name__)

# Row lengths in the same ballpark as addon catalog rows. History is fetched
# deeper because it lists plays, not titles — a binged season is one show.
ROW_LIMIT = 20
HISTORY_FETCH_LIMIT = 60

# Concurrent /meta lookups per row. Meta is cached 24h, so this only throttles
# the first fetch of a fresh session.
_POSTER_CONCURRENCY = 8

RECOMMENDED_MOVIES_TITLE = "Recommended Movies"
RECOMMENDED_SERIES_TITLE = "Recommended Series"
HISTORY_TITLE = "Recently Watched"


@dataclass(frozen=True, slots=True)
class TraktRow:
    """One Home row. `type` mirrors CatalogRow's for the movie/series filter
    tabs; "" (the mixed history row) shows only under All."""

    title: str
    type: str
    items: list[MediaItem]


class TraktRows:
    def __init__(self, account: TraktAccount, get_detail: GetDetail) -> None:
        self._account = account
        self._get_detail = get_detail

    async def __call__(self) -> list[TraktRow]:
        """The rows Trakt has content for; [] when not connected."""
        token = await self._account.ensure_token()
        if token is None or not self._account.client_id:
            return []
        client_id = self._account.client_id
        api = self._account.api
        rows: list[TraktRow] = []
        for title, type_, fetch in (
            (
                RECOMMENDED_MOVIES_TITLE,
                "movie",
                api.recommendations(client_id, token, "movie", ROW_LIMIT),
            ),
            (
                RECOMMENDED_SERIES_TITLE,
                "series",
                api.recommendations(client_id, token, "series", ROW_LIMIT),
            ),
            (HISTORY_TITLE, "", self._history_refs(client_id, token)),
        ):
            try:
                entries = _dedupe(await fetch)[:ROW_LIMIT]
            except TraktError as exc:
                _log.warning("Trakt row %r failed: %s", title, exc)
                continue
            if not entries:
                # An empty row is worse than no row.
                continue
            rows.append(TraktRow(title, type_, await self._items(entries)))
        return rows

    async def _history_refs(self, client_id: str, token: str) -> list[TraktListItem]:
        """History plays as row references: episodes collapse onto their show
        (a poster row addresses shows, not S2E5); _dedupe drops repeats."""
        plays = await self._account.api.history(client_id, token, HISTORY_FETCH_LIMIT)
        return [
            TraktListItem(media_type=p.media_type, imdb_id=p.imdb_id, title=p.title) for p in plays
        ]

    async def _items(self, entries: list[TraktListItem]) -> list[MediaItem]:
        sem = asyncio.Semaphore(_POSTER_CONCURRENCY)

        async def build(entry: TraktListItem) -> MediaItem:
            async with sem:
                return MediaItem(
                    id=entry.imdb_id,
                    type=entry.media_type,
                    name=entry.title,
                    poster=await self._poster(entry),
                )

        return list(await asyncio.gather(*(build(e) for e in entries)))

    async def _poster(self, entry: TraktListItem) -> str | None:
        try:
            detail = await self._get_detail(entry.media_type, entry.imdb_id)
        except GravitasError as exc:
            _log.info("no poster for Trakt row item %s: %s", entry.imdb_id, exc)
            return None
        return detail.poster


def rows_to_payload(rows: list[TraktRow]) -> dict[str, object]:
    """The rows as a JSON-safe snapshot, so the last session's rows can greet
    the user instantly on the next launch while fresh ones are fetched."""
    return {
        "rows": [
            {
                "title": row.title,
                "type": row.type,
                "items": [
                    {"id": i.id, "type": i.type, "name": i.name, "poster": i.poster}
                    for i in row.items
                ],
            }
            for row in rows
        ]
    }


def rows_from_payload(payload: object) -> list[TraktRow]:
    """Rebuild rows from a snapshot; anything malformed is dropped silently —
    a cache is never worth an error."""
    if not isinstance(payload, dict):
        return []
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        return []
    rows: list[TraktRow] = []
    for raw in raw_rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("title"), str):
            continue
        items: list[MediaItem] = []
        for entry in raw.get("items") or []:
            if not isinstance(entry, dict):
                continue
            media_id = entry.get("id")
            media_type = entry.get("type")
            if not isinstance(media_id, str) or media_type not in ("movie", "series"):
                continue
            poster = entry.get("poster")
            items.append(
                MediaItem(
                    id=media_id,
                    type=media_type,
                    name=str(entry.get("name") or ""),
                    poster=poster if isinstance(poster, str) else None,
                )
            )
        if items:
            rows.append(
                TraktRow(
                    title=raw["title"],
                    type=raw["type"] if isinstance(raw.get("type"), str) else "",
                    items=items,
                )
            )
    return rows


def _dedupe(entries: list[TraktListItem]) -> list[TraktListItem]:
    """First occurrence wins — history repeats a show once per play."""
    seen: set[str] = set()
    unique: list[TraktListItem] = []
    for entry in entries:
        if entry.imdb_id in seen:
            continue
        seen.add(entry.imdb_id)
        unique.append(entry)
    return unique
