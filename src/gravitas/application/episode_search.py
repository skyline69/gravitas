"""Finding episodes of a series by what the viewer remembers of them.

A title word ("dungeons"), a line of the synopsis ("rom-coms"), or a number:
"S2E3", "2x03", "e3" or a bare "3" name episodes by position, since a query
that is only a number is almost never a word from a title. Every season is
searched -- the point is not knowing which one -- and the matches come back in
the order the page lists seasons (Specials last).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from gravitas.domain.models import Video

# "s2e3", "s02 e03", "2x03", "e3", "3": an episode, its season optional.
_EPISODE = re.compile(r"^(?:s?(?P<season>\d+)[ex]|e)?(?P<episode>\d+)$")
# "s2": a whole season.
_SEASON = re.compile(r"^s(?P<season>\d+)$")


def _fold(text: str) -> str:
    """Lower case without accents, so "cafe" finds "Café"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _order(video: Video) -> tuple[bool, int, int]:
    season = video.season or 0
    return (season == 0, season, video.episode or 0)


def _by_number(videos: list[Video], query: str) -> list[Video]:
    if (season_only := _SEASON.match(query)) is not None:
        whole = int(season_only["season"])
        return [v for v in videos if (v.season or 0) == whole]
    if (number := _EPISODE.match(query)) is None:
        return []
    episode = int(number["episode"])
    season = int(number["season"]) if number["season"] is not None else None
    return [
        v for v in videos if v.episode == episode and (season is None or (v.season or 0) == season)
    ]


def find_episodes(videos: Iterable[Video], query: str) -> list[Video]:
    """The episodes `query` names, in page order. Empty for a blank query."""
    words = _fold(query).split()
    if not words:
        return []
    listed = list(videos)
    picked = _by_number(listed, "".join(words))
    if not picked:
        # A number can be a word, too ("1993", "22 Short Films").
        picked = [
            v for v in listed if all(w in _fold(f"{v.title} {v.overview or ''}") for w in words)
        ]
    return sorted(picked, key=_order)
