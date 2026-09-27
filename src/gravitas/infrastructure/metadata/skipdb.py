"""SegmentSource backed by SkipDB: crowd-sourced intro/recap/outro timestamps.

The fallback for files whose chapters do not name their sections (see
application/segments.py -- the file's own chapters always win, and this is not
asked when they cover everything). SkipDB keys episodes by IMDb id + season +
episode, which is what a Cinemeta-style video id already is, and takes the
file's duration so an answer recorded against another cut can be shifted onto
this one. Reading needs no key (120 requests/min); the data is ODbL.
https://skipdb.tv/docs

Every lookup tells SkipDB what is being watched, so each is made once:

* **In memory** for the session, keyed by episode and (rounded) duration, and
  concurrent asks for one key share a single request -- a reconnect or a
  replay never asks again.
* **On disk** (JsonDiskCache, beside the addon JSON), so a restart does not
  either. A found answer is trusted for FOUND_TTL_S, "nothing known" for the
  shorter MISSING_TTL_S: the data is crowd-sourced and fills in over time.
* **Failures are never cached.** A timeout or a 5xx says nothing about the
  episode, and the next playback asks again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

import httpx

from gravitas.domain.models import Segments

_log = logging.getLogger(__name__)

API = "https://api.skipdb.tv/api/segments"
FOUND_TTL_S = 7 * 24 * 60 * 60.0
MISSING_TTL_S = 24 * 60 * 60.0
# Below this SkipDB is guessing more than it knows (it reports 0..1).
MIN_CONFIDENCE = 0.5
# How long an intro or recap may plausibly run; SkipDB's own caps are larger.
MAX_OPENING_S = 300.0
# Durations within this many seconds share a cache entry: the same release
# reports the same length give or take a frame, and SkipDB's shift is
# computed against it anyway.
_DURATION_BUCKET_S = 5
_TIMEOUT_S = 8.0
# Answers SkipDB says do not fit this file's length at all.
_UNUSABLE_MATCHES = frozenset({"out-of-range"})


class _Cache(Protocol):
    """JsonDiskCache, as this needs it."""

    def get(self, url: str) -> tuple[dict[str, Any], float] | None: ...
    def put(self, url: str, data: dict[str, Any]) -> None: ...


def _interval(raw: object, duration: float) -> tuple[float, float] | None:
    """One SkipDB segment as (start, end) seconds, or None if it is absent,
    unsure, or does not fit this file."""
    if not isinstance(raw, dict):
        return None
    if raw.get("match") in _UNUSABLE_MATCHES:
        return None
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)) or confidence < MIN_CONFIDENCE:
        return None
    start_ms, end_ms = raw.get("start_ms"), raw.get("end_ms")
    if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
        return None
    start, end = start_ms / 1000, end_ms / 1000
    if not 0 <= start < end or (duration > 0 and end > duration + 1):
        return None
    return (start, end)


def parse(payload: dict[str, Any], duration: float) -> Segments:
    """A SkipDB answer as Segments. Outro is where the credits start; a
    closing "next time" preview stands in for it when there is no outro."""
    raw = payload.get("segments")
    if not isinstance(raw, dict):
        return Segments()
    intro = _interval(raw.get("intro"), duration)
    recap = _interval(raw.get("recap"), duration)
    if intro is not None and intro[1] - intro[0] > MAX_OPENING_S:
        intro = None
    if recap is not None and recap[1] - recap[0] > MAX_OPENING_S:
        recap = None
    outro = _interval(raw.get("outro"), duration)
    preview = _interval(raw.get("preview"), duration)
    credits_start: float | None = None
    if outro is not None and (duration <= 0 or outro[0] > duration * 0.5):
        credits_start = outro[0]
    elif preview is not None and duration > 0 and preview[0] > duration * 0.5:
        credits_start = preview[0]
    return Segments(intro=intro, recap=recap, credits_start=credits_start)


def _to_json(segments: Segments) -> dict[str, Any]:
    return {
        "intro": list(segments.intro) if segments.intro else None,
        "recap": list(segments.recap) if segments.recap else None,
        "credits_start": segments.credits_start,
    }


def _from_json(data: dict[str, Any]) -> Segments:
    def pair(value: object) -> tuple[float, float] | None:
        if isinstance(value, list) and len(value) == 2:
            return (float(value[0]), float(value[1]))
        return None

    credits = data.get("credits_start")
    return Segments(
        intro=pair(data.get("intro")),
        recap=pair(data.get("recap")),
        credits_start=float(credits) if isinstance(credits, (int, float)) else None,
    )


class SkipDbSource:
    """The SegmentSource port. Never raises; None means nothing known."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        disk: _Cache | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self._client = client
        self._disk = disk
        self._clock = clock
        self._memory: dict[str, tuple[Segments | None, float]] = {}
        self._inflight: dict[str, asyncio.Future[Segments | None]] = {}

    @staticmethod
    def _key(imdb_id: str, season: int, episode: int, duration: float) -> str:
        bucket = round(duration / _DURATION_BUCKET_S) * _DURATION_BUCKET_S
        return f"skipdb:{imdb_id}:{season}:{episode}:{bucket}"

    async def segments(
        self, imdb_id: str, season: int, episode: int, duration: float
    ) -> Segments | None:
        key = self._key(imdb_id, season, episode, duration)
        remembered = self._memory.get(key)
        if remembered is not None and self._fresh(remembered[0], self._clock() - remembered[1]):
            return remembered[0]
        running = self._inflight.get(key)
        if running is not None:
            try:
                return await asyncio.shield(running)
            except asyncio.CancelledError:
                if running.cancelled():
                    return None  # the lookup this shared was cancelled, not this ask
                raise
        future: asyncio.Future[Segments | None] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            result, cacheable = await self._resolve(key, imdb_id, season, episode, duration)
        except BaseException:
            # Cancelled (the viewer left) or a bug: whoever shares this lookup
            # gets no answer from it rather than a stale or foreign one.
            future.cancel()
            raise
        finally:
            self._inflight.pop(key, None)
        if cacheable:
            self._memory[key] = (result, self._clock())
        future.set_result(result)
        return result

    @staticmethod
    def _fresh(value: Segments | None, age: float) -> bool:
        return age < (FOUND_TTL_S if value is not None else MISSING_TTL_S)

    async def _resolve(
        self, key: str, imdb_id: str, season: int, episode: int, duration: float
    ) -> tuple[Segments | None, bool]:
        """The answer, and whether it may be cached (a failure may not)."""
        if self._disk is not None:
            stored = await asyncio.to_thread(self._disk.get, key)
            if stored is not None:
                data, age = stored
                segments = data.get("segments")
                found = bool(data.get("found"))
                # A found answer that cannot be read back is a miss, not a
                # "nothing known" to trust for a day.
                if not found or isinstance(segments, dict):
                    known = _from_json(segments) if isinstance(segments, dict) and found else None
                    if self._fresh(known, age):
                        return known, True
        params: dict[str, str | int] = {
            "imdb_id": imdb_id,
            "season": season,
            "episode": episode,
        }
        if duration > 0:
            params["duration"] = round(duration)
        try:
            response = await self._client.get(API, params=params, timeout=_TIMEOUT_S)
        except httpx.HTTPError as exc:
            _log.info("SkipDB unreachable for %s S%sE%s: %r", imdb_id, season, episode, exc)
            return None, False
        if response.status_code == 404:
            value: Segments | None = None
        elif response.status_code != 200:
            _log.info(
                "SkipDB answered %s for %s S%sE%s", response.status_code, imdb_id, season, episode
            )
            return None, False
        else:
            try:
                payload = response.json()
            except ValueError:
                return None, False
            parsed = parse(payload, duration) if isinstance(payload, dict) else Segments()
            value = parsed if parsed != Segments() else None
        if self._disk is not None:
            record = {"found": value is not None, "segments": _to_json(value or Segments())}
            await asyncio.to_thread(self._disk.put, key, record)
        _log.info(
            "SkipDB for %s S%sE%s: %s",
            imdb_id,
            season,
            episode,
            "nothing known" if value is None else value,
        )
        return value, True
