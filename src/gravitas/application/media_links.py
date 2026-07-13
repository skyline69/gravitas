"""Parse a pasted IMDB/TVDB link or id into a (source, external_id) pair."""

from __future__ import annotations

import re

_IMDB = re.compile(r"\b(tt\d{7,8})\b")
# A TVDB link must carry a numeric id: /series/<n>, /movies/<n>, or ?...id=<n>.
# Slug-only URLs (…/series/breaking-bad) have no id and are treated as text.
_TVDB_URL_ID = re.compile(
    r"thetvdb\.com/(?:[^?\s]*?/(?:series|movies)/(\d+)|[^\s]*?[?&]id=(\d+))",
    re.I,
)
_TVDB_PREFIX = re.compile(r"^\s*tvdb:(\d+)\s*$", re.I)


def parse_media_link(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    imdb = _IMDB.search(text)
    if imdb:
        return ("imdb", imdb.group(1))
    prefix = _TVDB_PREFIX.match(text)
    if prefix:
        return ("tvdb", prefix.group(1))
    if "thetvdb.com" in text.lower():
        m = _TVDB_URL_ID.search(text)
        if m:
            return ("tvdb", m.group(1) or m.group(2))
    return None
