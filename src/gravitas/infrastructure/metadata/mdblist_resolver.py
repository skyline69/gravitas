"""RatingsResolver backed by MDBList — RT + Letterboxd scores by IMDb id."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from gravitas.domain.errors import MdbListUnavailable
from gravitas.domain.models import MediaType, Ratings
from gravitas.infrastructure.cache.ttl_cache import TtlCache

# MDBList keys media by imdb id under a type-specific path, e.g.
# https://api.mdblist.com/imdb/movie/tt1375666?apikey=… -> {"ratings": [...]}
_API = "https://api.mdblist.com/imdb"
_TTL = 60 * 60 * 24  # ratings move slowly; 24h like META_TTL


def _number(value: Any) -> float | None:
    # bool is an int subclass — reject it so True/False never counts as a score.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Treat 0 as absent: MDBList's representation of a missing score is unverified,
    # so we prefer showing no pill over a possibly-spurious 0. Revisit (allow >= 0)
    # once live MDBList response is confirmed.
    return float(value) if value > 0 else None


def _score_pct(value: Any) -> str | None:
    n = _number(value)
    return str(round(n)) if n is not None else None


def _score_lb(value: Any) -> str | None:
    # MDBList Letterboxd `value` is out of 5 (verify against a live response;
    # if it is 0-10, divide by 2 here).
    n = _number(value)
    return f"{n:.1f}" if n is not None else None


def _parse_ratings(payload: dict[str, Any]) -> Ratings:
    by_source: dict[str, Any] = {}
    raw = payload.get("ratings")
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                src = entry.get("source")
                if isinstance(src, str):
                    by_source[src.lower()] = entry.get("value")

    rt = _score_pct(by_source.get("tomatoes"))
    fresh = (int(rt) >= 60) if rt is not None else None
    return Ratings(
        rotten_tomatoes=rt,
        rotten_tomatoes_fresh=fresh,
        letterboxd=_score_lb(by_source.get("letterboxd")),
    )


class MdbListResolver:
    def __init__(self, client: httpx.AsyncClient, get_key: Callable[[], str | None]) -> None:
        self._client = client
        self._get_key = get_key
        self._cache: TtlCache[Ratings] = TtlCache()

    async def ratings(self, imdb_id: str, media_type: MediaType) -> Ratings:
        key = self._get_key()
        if not key:
            raise MdbListUnavailable("add an MDBList API key in Settings for RT/Letterboxd")

        cached = self._cache.get(imdb_id)
        if cached is not None:
            return cached

        # MDBList's path segment is "show" for series, "movie" for films.
        kind = "show" if media_type == "series" else "movie"
        try:
            resp = await self._client.get(
                f"{_API}/{kind}/{imdb_id}", params={"apikey": key}, timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MdbListUnavailable(f"MDBList request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise MdbListUnavailable("unexpected MDBList response")

        ratings = _parse_ratings(data)
        self._cache.put(imdb_id, ratings, _TTL)
        return ratings
