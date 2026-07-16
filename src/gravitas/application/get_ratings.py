"""Use case: fetch external ratings (RT + Letterboxd) for a title by imdb id.

Fault-isolated: any GravitasError becomes an empty Ratings so a ratings outage
never breaks the Detail page."""

from __future__ import annotations

from gravitas.domain.errors import GravitasError
from gravitas.domain.models import Ratings
from gravitas.domain.ports import RatingsResolver


class GetRatings:
    def __init__(self, resolver: RatingsResolver) -> None:
        self._resolver = resolver

    async def __call__(self, imdb_id: str) -> Ratings:
        try:
            return await self._resolver.ratings(imdb_id)
        except GravitasError:
            return Ratings()
