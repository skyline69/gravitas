import asyncio
from pathlib import Path

import httpx
import respx

from gravitas.domain.models import Segments
from gravitas.infrastructure.cache.json_disk_cache import JsonDiskCache
from gravitas.infrastructure.metadata.skipdb import (
    API,
    FOUND_TTL_S,
    MISSING_TTL_S,
    SkipDbSource,
    parse,
)

# Breaking Bad S1E1 as SkipDB answered it (measured).
BREAKING_BAD = {
    "imdb_id": "tt0903747",
    "season": 1,
    "episode": 1,
    "segments": {
        "intro": {
            "start_ms": 229500,
            "end_ms": 246500,
            "adjusted": False,
            "offset_ms": 0,
            "match": "agnostic",
            "confidence": 0.75,
        },
        "recap": None,
        "outro": {
            "start_ms": 3434000,
            "end_ms": 3500000,
            "adjusted": False,
            "offset_ms": 0,
            "match": "agnostic",
            "confidence": 0.75,
        },
        "preview": None,
    },
}
NOTHING = {
    "imdb_id": "tt14824792",
    "season": 1,
    "episode": 7,
    "segments": {"intro": None, "recap": None, "outro": None, "preview": None},
}


def test_parse_the_measured_answer() -> None:
    assert parse(BREAKING_BAD, 3500.0) == Segments(
        intro=(229.5, 246.5), recap=None, credits_start=3434.0
    )
    assert parse(NOTHING, 2559.0) == Segments()


def test_parse_drops_what_it_should_not_believe() -> None:
    def one(**intro: object) -> dict[str, object]:
        base = {"start_ms": 10000, "end_ms": 40000, "match": "exact", "confidence": 0.9}
        return {"segments": {"intro": {**base, **intro}}}

    assert parse(one(), 2600.0).intro == (10.0, 40.0)
    assert parse(one(confidence=0.3), 2600.0).intro is None  # a guess
    assert parse(one(match="out-of-range"), 2600.0).intro is None  # another cut
    assert parse(one(end_ms=900000), 2600.0).intro is None  # 15 minutes of "intro"
    assert parse(one(start_ms=50000), 2600.0).intro is None  # ends before it starts
    # A "next time" preview stands in for the credits only at the end.
    preview = {"start_ms": 2500000, "end_ms": 2560000, "match": "exact", "confidence": 0.8}
    assert parse({"segments": {"preview": preview}}, 2600.0).credits_start == 2500.0
    early = {**preview, "start_ms": 30000, "end_ms": 60000}
    assert parse({"segments": {"preview": early}}, 2600.0).credits_start is None


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@respx.mock
async def test_one_request_per_episode_however_it_is_asked() -> None:
    """Every lookup tells SkipDB what is being watched: a replay, a reconnect
    and two asks at once all share the first answer."""
    route = respx.get(API).mock(return_value=httpx.Response(200, json=BREAKING_BAD))
    async with httpx.AsyncClient() as http:
        source = SkipDbSource(http)
        together = await asyncio.gather(
            source.segments("tt0903747", 1, 1, 3500.0),
            source.segments("tt0903747", 1, 1, 3500.0),
        )
        again = await source.segments("tt0903747", 1, 1, 3501.0)  # same release, a frame apart
    assert together[0] == together[1] == again
    assert again is not None and again.intro == (229.5, 246.5)
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.url.params["imdb_id"] == "tt0903747"
    assert request.url.params["duration"] == "3500"


@respx.mock
async def test_answers_survive_a_restart_on_disk(tmp_path: Path) -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=BREAKING_BAD))
    disk = JsonDiskCache(tmp_path / "cache.db")
    async with httpx.AsyncClient() as http:
        first = await SkipDbSource(http, disk).segments("tt0903747", 1, 1, 3500.0)
        # A new session: new memory, same disk.
        second = await SkipDbSource(http, disk).segments("tt0903747", 1, 1, 3500.0)
    assert first == second
    assert route.call_count == 1


@respx.mock
async def test_nothing_known_is_remembered_for_less_time(tmp_path: Path) -> None:
    """Crowd-sourced data fills in: "nothing yet" is asked again after a day,
    a found answer after a week."""
    route = respx.get(API).mock(return_value=httpx.Response(200, json=NOTHING))
    wall = Clock()
    disk = JsonDiskCache(tmp_path / "cache.db", clock=wall)
    async with httpx.AsyncClient() as http:
        source = SkipDbSource(http, disk, clock=wall)
        assert await source.segments("tt14824792", 1, 7, 2559.0) is None
        assert await source.segments("tt14824792", 1, 7, 2559.0) is None
        assert route.call_count == 1
        wall.now += MISSING_TTL_S + 1
        await source.segments("tt14824792", 1, 7, 2559.0)
        assert route.call_count == 2

        route.mock(return_value=httpx.Response(200, json=BREAKING_BAD))
        await source.segments("tt0903747", 1, 1, 3500.0)
        wall.now += MISSING_TTL_S + 1  # past "nothing"'s lifetime, well inside "found"'s
        await source.segments("tt0903747", 1, 1, 3500.0)
        assert route.call_count == 3
        wall.now += FOUND_TTL_S
        await source.segments("tt0903747", 1, 1, 3500.0)
        assert route.call_count == 4


@respx.mock
async def test_a_failure_is_not_remembered(tmp_path: Path) -> None:
    """A timeout or a 5xx says nothing about the episode."""
    route = respx.get(API).mock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(503),
            httpx.Response(200, json=BREAKING_BAD),
        ]
    )
    disk = JsonDiskCache(tmp_path / "cache.db")
    async with httpx.AsyncClient() as http:
        source = SkipDbSource(http, disk)
        assert await source.segments("tt0903747", 1, 1, 3500.0) is None
        assert await source.segments("tt0903747", 1, 1, 3500.0) is None
        found = await source.segments("tt0903747", 1, 1, 3500.0)
    assert found is not None
    assert route.call_count == 3


async def test_a_corrupt_disk_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    disk = JsonDiskCache(tmp_path / "cache.db")
    disk.put("skipdb:tt0903747:1:1:3500", {"found": True, "segments": "garbage"})
    with respx.mock:
        route = respx.get(API).mock(return_value=httpx.Response(200, json=BREAKING_BAD))
        async with httpx.AsyncClient() as http:
            assert await SkipDbSource(http, disk).segments("tt0903747", 1, 1, 3500.0) is not None
    assert route.call_count == 1
