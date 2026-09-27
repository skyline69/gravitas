import pytest

from gravitas.application.get_ratings import GetRatings
from gravitas.domain.errors import MdbListUnavailable
from gravitas.domain.models import Ratings


class _FakeResolver:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def ratings(self, imdb_id: str, media_type: str) -> Ratings:
        self.calls.append((imdb_id, media_type))
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_returns_resolved_ratings():
    resolver = _FakeResolver(result=Ratings(rotten_tomatoes="87", letterboxd="4.1"))
    get = GetRatings(resolver)
    r = await get("tt1375666", "movie")
    assert r.rotten_tomatoes == "87"
    assert resolver.calls == [("tt1375666", "movie")]


@pytest.mark.asyncio
async def test_swallows_gravitas_error_into_empty():
    get = GetRatings(_FakeResolver(error=MdbListUnavailable("down")))
    assert await get("tt1375666", "movie") == Ratings()
