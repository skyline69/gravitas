"""Rank search results by relevance to the query.

Primary signal is how well the title matches the query (some addons return
unfiltered popular lists for any query, which this pushes to the bottom);
IMDB rating is the tiebreaker within a match tier.
"""

from __future__ import annotations

import re

from gravitas.domain.models import MediaItem


def _match_tier(query: str, name: str) -> int:
    n = name.lower()
    if n == query:
        return 0
    if n.startswith(query):
        return 1
    if re.search(rf"\b{re.escape(query)}\b", n):
        return 2
    if query in n:
        return 3
    return 4


def _rating(item: MediaItem) -> float:
    try:
        return float(item.imdb_rating) if item.imdb_rating else 0.0
    except ValueError:
        return 0.0


def rank(query: str, items: list[MediaItem]) -> list[MediaItem]:
    q = query.lower().strip()
    if not q:
        return items
    # Stable sort: within a tier, higher rating first; equal ratings keep the
    # addon's original order.
    return sorted(items, key=lambda it: (_match_tier(q, it.name), -_rating(it)))
