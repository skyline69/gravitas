"""What this machine has been observed to decode smoothly, and what it has not.

A recommendation that ignores the decoder is a recommendation to stutter. The
machine's capacity cannot be read off a spec sheet the app does not have --
core count says nothing about whether libmpv's ffmpeg was built with this
codec's hwaccel, and `hwdec-current` says hardware decoding engaged, not that
it kept up. The one thing that settles it is what happened: mpv counts the
frames it dropped, and a machine that drops frames on 2160p AV1 will do it
again tomorrow.

So this is a record of evidence, in the same shape as
`application.compatibility`: a verdict from the player outranks any guess, and
there is no guess here at all. With nothing observed, nothing is claimed --
a fresh install demotes no source, which is the honest answer rather than a
cautious one.

Buckets are (codec family, frame height), not releases. "This machine drops
frames on 4K HEVC" generalises correctly to the next 4K HEVC file; "this
release stuttered" does not generalise at all, and a debrid URL cannot
identify a release tomorrow anyway.

The two thresholds are deliberately apart (see DECODE_STRAIN_DPM /
DECODE_SMOOTH_DPM): between them, a bucket keeps the verdict it already had.
A single boundary would flip a marginal machine's verdict every other episode,
and a recommendation that moves for no reason the viewer can see is worse than
one that is slightly stale.
"""

from __future__ import annotations

import re

from gravitas.domain.models import (
    DECODE_OBSERVATION_MIN_S,
    DECODE_SMOOTH_DPM,
    DECODE_STRAIN_DPM,
    DecodeReport,
)

# Codec families worth telling apart, newest-first so "HEVC" cannot be matched
# by a substring of something else. mpv's video-codec string is descriptive
# ("HEVC (High Efficiency Video Coding)"), and addon labels use the release
# scene's names, so both vocabularies are listed for each family.
_CODEC_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("av1", ("av1", "av01")),
    ("hevc", ("hevc", "h265", "h.265", "x265", "265")),
    ("vp9", ("vp9", "vp09")),
    ("h264", ("h264", "h.264", "x264", "avc", "264")),
)
_WORD_RE = re.compile(r"[0-9a-z.]+")

# Heights a bucket can be filed under. A decoder's cost tracks pixels, so an
# observation at 2160p speaks for 2160p files and a 1080p one does not --
# rounding to the tier below keeps an odd frame size (1920x816 letterbox,
# 3840x1600) with the tier it belongs to.
_TIERS = (2160, 1440, 1080, 720, 480)


def codec_family(text: str) -> str:
    """The codec family named by a decoder string or an addon label, or "".

    Empty is a real answer: a label that names no codec must not be filed
    under one, because a verdict about the wrong family is worse than none.
    """
    words = set(_WORD_RE.findall(text.lower()))
    for family, names in _CODEC_FAMILIES:
        if words & set(names):
            return family
    return ""


def height_tier(height: int) -> int:
    """The tier a frame height belongs to: the highest tier it reaches, or 0
    when it reaches none (below 480p, or not known yet)."""
    for tier in _TIERS:
        if height >= tier:
            return tier
    return 0


def bucket_key(codec: str, height: int) -> str:
    """The identifier for "this codec at this size", or "" when either half is
    missing -- half an observation is not one."""
    family = codec_family(codec)
    tier = height_tier(height)
    if not family or not tier:
        return ""
    return f"{family}:{tier}"


def drops_per_minute(dropped: int, played_s: float) -> float:
    """Dropped frames per minute of playback. 0.0 for a playback too short to
    say anything -- see DECODE_OBSERVATION_MIN_S."""
    if played_s < DECODE_OBSERVATION_MIN_S:
        return 0.0
    return dropped / (played_s / 60.0)


class PlaybackCapability:
    """The buckets this machine is currently judged to struggle with.

    Only strained buckets are stored. "Not in the set" covers both "played
    fine" and "never tried", and those two do not need telling apart: neither
    is a reason to demote a source.
    """

    def __init__(self) -> None:
        self.strained: tuple[str, ...] = ()

    def strained_keys(self) -> frozenset[str]:
        return frozenset(self.strained)

    def is_strained(self, codec: str, height: int) -> bool:
        key = bucket_key(codec, height)
        return bool(key) and key in self.strained

    def observe(self, report: DecodeReport, played_s: float) -> bool:
        """File one playback's evidence. Returns whether the verdict changed,
        so the caller persists only when there is something new to persist.

        A playback shorter than DECODE_OBSERVATION_MIN_S changes nothing: the
        frames dropped while a seek settles or a cache fills are not a
        statement about the decoder.
        """
        key = bucket_key(report.codec, report.height)
        if not key or played_s < DECODE_OBSERVATION_MIN_S:
            return False
        rate = drops_per_minute(report.dropped_frames, played_s)
        if rate >= DECODE_STRAIN_DPM and key not in self.strained:
            self.strained = (*self.strained, key)
            return True
        if rate < DECODE_SMOOTH_DPM and key in self.strained:
            # Proven smooth since: a driver update, a lighter encode of the
            # same size, or a machine that was busy the first time.
            self.strained = tuple(k for k in self.strained if k != key)
            return True
        return False
