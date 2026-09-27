"""Pick the few sources worth starting with, and say why.

A source list is fifty rows that differ in eight dimensions at once. Ranking
them (see `stream_ranking`) answers "which order", which still leaves the
viewer reading fifty rows. This answers the smaller, more useful question:
**for each picture this screen can actually show, which one row should I
click?**

A recommendation is a claim, so each one is made only from evidence the app
holds:

* **The screen** (`infrastructure/display.py`) decides what "each resolution"
  even means. Tiers are the picture that reaches the panel, not the number in
  the label -- on a 1080p laptop a 2160p source and a 1080p source produce the
  identical image, so recommending both would offer a choice that is not one.
* **The connection** (`application/connection_speed.py`) is a ceiling. A row
  that cannot stream is not a recommendation whatever it looks like. Until
  something has been measured the ceiling falls back to a typical rate for the
  tier, which keeps an 80 Mbps remux from being recommended blind.
* **The decoder** (`application/playback_capability.py`) is the other ceiling,
  and the one nothing else here models: a machine that drops frames on 4K AV1
  must not be sent to a 4K AV1 file, however well it fits the line.
* **The renderer** (`application/compatibility.py`) removes what cannot be
  displayed correctly at all. This applies even with the hide-incompatible
  setting off: that setting governs whether a broken source is *listed*, and
  listing it is a long way from recommending it.

What is left is ranked inside its tier by things that change the outcome of a
click: a cached source plays now, a native one costs less to show the same
picture, a higher bitrate inside the ceiling is a better encode.

Nothing here filters or reorders on its own -- it returns which rows earned a
mark and the sentence behind each. With no evidence, it returns nothing, and
an unmarked list is the correct output for a machine that has been told
nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gravitas.application.compatibility import is_incompatible
from gravitas.application.playback_capability import codec_family, height_tier
from gravitas.application.stream_ranking import (
    HEADROOM,
    NOMINAL_MBPS,
    delivered_height,
    estimate_bitrate_mbps,
    is_oversized,
    parse_resolution,
)
from gravitas.domain.models import Stream

# At most this many rows are marked. The point is to shorten the decision; a
# list where a third of the rows are "recommended" has not shortened anything.
MAX_PICKS = 3

# Multiple of a tier's nominal rate that a source may cost when no bandwidth
# has been measured. Above it the file is a remux or a heavy encode, and
# recommending one on an unknown line is a guess with a stall attached.
UNMEASURED_CEILING = 1.6

# A second pick in the same tier has to be materially cheaper to be a choice
# rather than a duplicate: it must cost no more than this fraction of the
# first pick's bitrate.
LIGHTER_FRACTION = 0.6

# Multiple of a tier's nominal rate past which extra bits stop being visible
# at that tier. 1.0 is deliberate rather than generous: the tier is the
# picture the PANEL shows, so a 4K source on a 1440p screen is resampled down
# before anyone sees it, and the bits it spent on detail finer than the panel
# are thrown away by the scaler. Paying 25 Mbps for a picture a 14 Mbps source
# delivers identically is how a recommendation ends up buffering.
USEFUL_MULTIPLE = 1.0

_INSTANT_RE = re.compile(r"\b(instant|cached)\b", re.IGNORECASE)


class SourceRecommendations:
    """The toggle, and how many rows it may mark.

    On by default, unlike the sort and the compatibility filter: those two
    change or remove rows the user asked for, while this one adds a mark and
    moves the marked rows to the top of a list nobody has read yet. With
    nothing measured and nothing learned it still marks the best row per
    quality, which is the worst case and is already useful.
    """

    def __init__(self) -> None:
        self.enabled: bool = True
        self.limit: int = MAX_PICKS


@dataclass(frozen=True, slots=True)
class Recommendation:
    """One marked row: where it is in the list the caller passed in, and the
    sentence that justifies the mark."""

    index: int
    reason: str


def is_instant(stream: Stream) -> bool:
    """Whether the addon says this source is already cached and will start
    immediately. The lightning bolt is the convention debrid addons use; the
    words are there for the ones that spell it out."""
    text = f"{stream.name} {stream.title}"
    return "⚡" in text or bool(_INSTANT_RE.search(text))


def star_rating(stream: Stream) -> int:
    return f"{stream.name} {stream.title}".count("★")


def tier_label(height: int) -> str:
    """How a tier is named to a person. 2160p is "4K" everywhere else in this
    UI, and a recommendation that calls it something else reads as being about
    a different row."""
    if height >= 2160:
        return "4K"
    return f"{height}p"


def _nominal_for(tier: int) -> float:
    """The nominal rate for a delivered tier.

    Through height_tier, not a direct lookup: a tier is capped by the panel,
    and a panel is whatever it is (a 14" MacBook reports 1964 physical pixels).
    A raw `NOMINAL_MBPS.get(1964)` misses and falls back to the "unknown"
    entry, which is lower than 1440p's -- so an odd panel height silently
    became the strictest screen in the table.
    """
    return NOMINAL_MBPS.get(height_tier(tier), NOMINAL_MBPS[0])


def _ceiling_mbps(tier: int, kbps: int | None) -> float:
    """The most a source in this tier may cost and still be recommendable."""
    if kbps is not None:
        return (kbps / 1000.0) * HEADROOM
    return _nominal_for(tier) * UNMEASURED_CEILING


def useful_mbps(tier: int, mbps: float) -> float:
    """How much of this source's bitrate reaches the viewer's eyes.

    Clamped at what the delivered tier can show (see USEFUL_MULTIPLE): past
    that the extra bits describe detail the scaler discards, so they are not
    quality, they are only cost.
    """
    return min(mbps, _nominal_for(tier) * USEFUL_MULTIPLE)


def _reason(tier: int, kbps: int | None, *, instant: bool) -> str:
    """The sentence shown on the mark, built from what was actually known.

    Assembled as separate sentences rather than one clause chain: the caveats
    are independent, any combination of them can apply, and a reason the
    viewer has to parse twice is not a reason.
    """
    label = tier_label(tier)
    if kbps is not None:
        sentences = [f"Best {label} your connection can stream (about {round(kbps / 1000)} Mbps)."]
    else:
        sentences = [f"Best {label} on offer."]
    if instant:
        sentences.append("It starts instantly.")
    if kbps is None:
        sentences.append("Your connection hasn't been measured yet.")
    return " ".join(sentences)


def recommend(
    streams: list[Stream],
    *,
    kbps: int | None = None,
    display_height: int = 0,
    runtime_s: float | None = None,
    known_bad: frozenset[str] | set[str] = frozenset(),
    strained: frozenset[str] | set[str] = frozenset(),
    limit: int = MAX_PICKS,
) -> tuple[Recommendation, ...]:
    """Which rows of `streams` to mark, best tier first.

    `strained` holds codec:height buckets this machine drops frames in (see
    `playback_capability.bucket_key`). `known_bad` holds label signatures the
    player caught rendering wrong. Both are evidence; neither is required, and
    empty means only that nothing has been learned yet.

    Indices are positions in `streams` exactly as passed, so the caller can
    reorder afterwards without the marks following the wrong rows.
    """
    if not streams or limit <= 0:
        return ()

    # (tier, index, bitrate, sort key) for every row that could be clicked
    # without a known reason to regret it.
    tiers: dict[int, list[tuple[tuple[bool, bool, float, float, int, int], int, float]]] = {}
    for index, stream in enumerate(streams):
        if not stream.is_direct:
            continue  # a web page is not a source; it cannot be recommended
        if is_incompatible(stream, known_bad):
            continue
        tier = delivered_height(stream, display_height)
        if tier <= 0:
            continue  # nothing names its quality: no tier to be the best of
        if _is_strained(stream, strained):
            continue
        mbps = estimate_bitrate_mbps(stream, runtime_s=runtime_s)
        if mbps > _ceiling_mbps(tier, kbps):
            continue
        key = (
            not is_instant(stream),
            is_oversized(stream, display_height),
            # More bits, but only while they still show. Once two sources both
            # saturate what the tier can display, the cheaper one wins: it
            # puts the same picture on the panel and is far less likely to be
            # the source that starts buffering twenty minutes in.
            -useful_mbps(tier, mbps),
            mbps,
            -star_rating(stream),
            index,
        )
        tiers.setdefault(tier, []).append((key, index, mbps))

    if not tiers:
        return ()

    picks: list[Recommendation] = []
    for tier in sorted(tiers, reverse=True)[:limit]:
        _, index, _ = min(tiers[tier])
        reason = _reason(tier, kbps, instant=is_instant(streams[index]))
        picks.append(Recommendation(index=index, reason=reason))

    # One tier means one pick, which on a laptop panel is the common case:
    # every 4K and 1080p source collapses into the same delivered picture. The
    # choice that is left there is what it costs, so offer the cheap one too --
    # same image, a fraction of the bandwidth.
    if len(picks) == 1 and limit > 1:
        lighter = _lighter_alternative(tiers[max(tiers)], picks[0].index)
        if lighter is not None:
            picks.append(lighter)
    return tuple(picks)


def _is_strained(stream: Stream, strained: frozenset[str] | set[str]) -> bool:
    """Whether this source's own codec, at its own resolution, is one this
    machine has been caught dropping frames in.

    The label's resolution is used rather than the delivered one: the decoder
    works on the frames in the file, and downscaling happens after it.
    A label naming no codec is not strained -- an unnamed codec cannot match a
    bucket, and guessing one would demote sources on no evidence at all.
    """
    if not strained:
        return False
    text = f"{stream.name} {stream.title}"
    family = codec_family(text)
    height = parse_resolution(text)
    if not family or height <= 0:
        return False
    return f"{family}:{height}" in strained


def _lighter_alternative(
    tier_rows: list[tuple[tuple[bool, bool, float, float, int, int], int, float]], chosen: int
) -> Recommendation | None:
    """The best row in the same tier that costs materially less than the pick,
    or None when every alternative is within a rounding error of it."""
    top = next((mbps for _, index, mbps in tier_rows if index == chosen), None)
    if top is None or top <= 0:
        return None
    budget = top * LIGHTER_FRACTION
    cheaper = [row for row in tier_rows if row[1] != chosen and row[2] <= budget]
    if not cheaper:
        return None
    _, index, mbps = min(cheaper)
    return Recommendation(
        index=index,
        reason=f"Same picture for less: about {mbps:.0f} Mbps instead of {top:.0f} Mbps.",
    )
