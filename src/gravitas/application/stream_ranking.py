"""Order the Sources list by what this connection can actually sustain.

Two bands, never a filter: streams whose bitrate fits the measured bandwidth
come first (best quality at the top), everything heavier follows in the same
order. Nothing is hidden -- a source that will buffer is still one click away,
it just stops being the first thing under the cursor.

Quality means the picture that reaches the screen, not the number in the
label. A 4K source on a 1440p panel is downscaled to 1440p before anyone sees
it, so it ranks as a 1440p source -- and below a native 1440p one, which
delivers the same picture for a fraction of the bandwidth. It stays above
everything that delivers less; being oversized is not a fault, it is just not
an advantage.

Bitrate is read out of the addon's own label, which is free-form text ("... ❖
11.4 GB/22.8 GB · 25.6 Mbps ⛨ ..."). An explicit rate is taken as given; a
size is turned into a rate when the runtime is known; otherwise the resolution
stands in. That last fallback is a guess, and it is why the bands are drawn
with headroom rather than at the exact measured number.
"""

from __future__ import annotations

import re

from gravitas.domain.models import Stream

# Fraction of measured bandwidth a stream may ask for and still count as
# fitting. Leaves room for the estimate being optimistic, for the bitrate
# being an average over a file whose peaks are higher, and for whatever else
# on the network wants throughput while you watch.
HEADROOM = 0.8

# Nominal bitrates (Mbps) for a resolution whose label says nothing else.
# Deliberately middle-of-the-road for streaming releases rather than worst
# case: a remux at 80 Mbps usually states its bitrate or its size, and both
# beat this table.
NOMINAL_MBPS = {2160: 25.0, 1440: 14.0, 1080: 8.0, 720: 4.0, 480: 1.5, 0: 6.0}

_RES_RE = re.compile(r"\b(2160p|1440p|1080p|720p|480p|360p|4k|uhd)\b", re.IGNORECASE)
_RES_HEIGHT = {
    "4K": 2160,
    "UHD": 2160,
    "2160P": 2160,
    "1440P": 1440,
    "1080P": 1080,
    "720P": 720,
    "480P": 480,
    "360P": 360,
}
_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(gbps|mbps|kbps)\b", re.IGNORECASE)
_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(gib|gb|mib|mb)\b", re.IGNORECASE)
_RATE_TO_MBPS = {"gbps": 1000.0, "mbps": 1.0, "kbps": 0.001}
_SIZE_TO_MEGABITS = {
    "gb": 8000.0,
    "gib": 8 * 1024.0,
    "mb": 8.0,
    "mib": 8 * 1024.0 / 1000.0,
}


def _number(raw: str) -> float:
    # Addons written for a comma-decimal locale say "11,4 GB".
    return float(raw.replace(",", "."))


def parse_resolution(text: str) -> int:
    """Frame height in pixels named by the label, or 0 when it names none."""
    match = _RES_RE.search(text)
    if match is None:
        return 0
    return _RES_HEIGHT.get(match.group(1).upper(), 0)


def parse_bitrate_mbps(text: str, *, runtime_s: float | None = None) -> float | None:
    """The bitrate the label states, or implies through its size. None when it
    says neither -- a caller that needs a number falls back on the resolution."""
    rate = _RATE_RE.search(text)
    if rate is not None:
        value = _number(rate.group(1)) * _RATE_TO_MBPS[rate.group(2).lower()]
        if value > 0:
            return value
    if runtime_s and runtime_s > 0:
        size = _SIZE_RE.search(text)
        if size is not None:
            megabits = _number(size.group(1)) * _SIZE_TO_MEGABITS[size.group(2).lower()]
            if megabits > 0:
                return megabits / runtime_s
    return None


_RUNTIME_RE = re.compile(r"(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b", re.I)
_RUNTIME_UNIT_S = {
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
}


def parse_runtime_seconds(text: str) -> float | None:
    """Seconds named by a meta runtime string ("56 min", "2h 15m"), or None.

    Only used to turn a stated file size into a bitrate, so a runtime that
    cannot be read costs a fallback, never a wrong number.
    """
    if not text:
        return None
    total = 0
    for value, unit in _RUNTIME_RE.findall(text):
        total += int(value) * _RUNTIME_UNIT_S[unit.lower()]
    return float(total) if total > 0 else None


def estimate_bitrate_mbps(stream: Stream, *, runtime_s: float | None = None) -> float:
    """What this source is expected to cost, in Mbps. Always a number: an
    unknown bitrate is the resolution's nominal rate, not an exemption from
    the comparison."""
    text = f"{stream.name} {stream.title}"
    stated = parse_bitrate_mbps(text, runtime_s=runtime_s)
    if stated is not None:
        return stated
    height = parse_resolution(text)
    return NOMINAL_MBPS.get(height, NOMINAL_MBPS[0])


def delivered_height(stream: Stream, display_height: int) -> int:
    """The height this source actually puts on the screen.

    Capped by the panel, because that is where the picture ends up. A
    display_height of 0 means the screen is unknown, and an unknown screen
    caps nothing -- guessing small would demote every good source on a machine
    Qt could not answer for.
    """
    height = parse_resolution(f"{stream.name} {stream.title}")
    if display_height <= 0:
        return height
    return min(height, display_height) if height else height


def rank_streams(
    streams: list[Stream],
    kbps: int | None,
    *,
    runtime_s: float | None = None,
    display_height: int = 0,
) -> tuple[list[Stream], tuple[bool, ...]]:
    """Reorder `streams` into (fits, over-budget), each best-quality-first.

    Returns the streams and, positionally, whether each one is over budget.
    `kbps` None means nothing has been measured yet: the addon's own order is
    returned untouched and nothing is marked, because marking rows against a
    bandwidth nobody measured would be an invention.
    """
    if kbps is None or not streams:
        return list(streams), tuple(False for _ in streams)

    budget_mbps = (kbps / 1000.0) * HEADROOM
    costed = [(s, estimate_bitrate_mbps(s, runtime_s=runtime_s)) for s in streams]
    # Stable sort, four parts:
    #   1. band       -- what fits the connection, then what does not
    #   2. delivered  -- the picture that reaches the panel, best first
    #   3. oversized  -- at equal delivered picture, the source that did not
    #                    need downscaling wins; it spends less to show the same
    #                    thing, and downscaling is not a feature
    #   4. bitrate    -- within all of that, the better encode
    # Equal on all four keeps addon order, which is the addon's own opinion
    # about its sources and worth preserving.
    ordered = sorted(
        costed,
        key=lambda pair: (
            pair[1] > budget_mbps,
            -delivered_height(pair[0], display_height),
            is_oversized(pair[0], display_height),
            -pair[1],
        ),
    )
    return [s for s, _ in ordered], tuple(mbps > budget_mbps for _, mbps in ordered)


def is_oversized(stream: Stream, display_height: int) -> bool:
    """Whether this source carries more pixels than the screen can show."""
    if display_height <= 0:
        return False
    height = parse_resolution(f"{stream.name} {stream.title}")
    return height > display_height
