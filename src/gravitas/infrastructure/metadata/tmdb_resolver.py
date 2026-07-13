"""ExternalIdResolver backed by the TMDB /find API (imdb/tvdb -> imdb id + preview)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from gravitas.domain.errors import TmdbUnavailable
from gravitas.domain.models import MediaType, ResolvedMedia

_API = "https://api.themoviedb.org/3"
_IMG = "https://image.tmdb.org/t/p/w342"


class TmdbResolver:
    def __init__(self, client: httpx.AsyncClient, get_key: Callable[[], str | None]) -> None:
        self._client = client
        self._get_key = get_key

    async def _get(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            resp = await self._client.get(url, params=params, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TmdbUnavailable(f"TMDB request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise TmdbUnavailable("unexpected TMDB response")
        return data

    async def resolve(self, source: str, external_id: str) -> ResolvedMedia:
        key = self._get_key()
        if not key:
            raise TmdbUnavailable("add a TMDB API key in Settings to open TVDB links")

        found = await self._get(
            f"{_API}/find/{external_id}",
            {"external_source": f"{source}_id", "api_key": key},
        )
        movie = found.get("movie_results") or []
        tv = found.get("tv_results") or []
        if movie:
            entry, media_type, kind, date_key = movie[0], "movie", "movie", "release_date"
        elif tv:
            entry, media_type, kind, date_key = tv[0], "series", "tv", "first_air_date"
        else:
            raise TmdbUnavailable(f"TMDB found nothing for {source}:{external_id}")

        imdb_id = external_id if source == "imdb" else await self._imdb_id(kind, entry.get("id"))

        poster_path = entry.get("poster_path")
        poster = f"{_IMG}{poster_path}" if poster_path else None
        raw_date = entry.get(date_key) or ""
        year = raw_date[:4] or None
        name = str(entry.get("title") or entry.get("name") or "")
        m_type: MediaType = "movie" if media_type == "movie" else "series"
        return ResolvedMedia(imdb_id=imdb_id, type=m_type, name=name, poster=poster, year=year)

    async def _imdb_id(self, kind: str, tmdb_id: Any) -> str:
        key = self._get_key()
        data = await self._get(f"{_API}/{kind}/{tmdb_id}/external_ids", {"api_key": str(key)})
        imdb_id = data.get("imdb_id")
        if not imdb_id:
            raise TmdbUnavailable("TMDB entry has no IMDB id")
        return str(imdb_id)
