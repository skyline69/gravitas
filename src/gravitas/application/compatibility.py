"""Which sources this machine cannot display correctly.

One thing qualifies today: Dolby Vision profile 5. Its IPT-C2 colour is
converted by libplacebo alone, and libmpv's render API -- the only way video
reaches a Qt scene -- runs the older vo_gpu renderer, so a profile 5 file
plays magenta here on every platform and every backend. Nothing about the
machine changes that, which is what makes hiding such a source honest rather
than opinionated: it is not "worse", it is broken.

The addon's label cannot state the profile, so two signals stand in:

* **Shape of the label.** Profile 8.1 carries an HDR10 base layer and says so
  ("HEVC ✦ DV · HDR10"); profile 5 has no base layer to name ("HEVC ✦ DV").
  A guess, and wrong on a sloppy label -- which is why hidden sources stay
  one click from view rather than being dropped.
* **What mpv reported.** Once a file has played, its profile is a fact. The
  label it came from is remembered, and that source is never offered again.

A remembered verdict always outranks the guess: a label the heuristic
suspects, which then played as profile 8, is proven good.
"""

from __future__ import annotations

import re

from gravitas.domain.models import Stream

# The profile mpv cannot render correctly through the render API.
UNSUPPORTED_DV_PROFILE = 5

_DV_RE = re.compile(r"\b(dolby\s*vision|dovi|dv)\b", re.IGNORECASE)
# A base layer any renderer understands. HDR10+ and HLG count: whatever the DV
# layer adds, there is something underneath to fall back to.
_BASE_LAYER_RE = re.compile(r"\b(hdr10\+?|hlg|sdr)\b", re.IGNORECASE)
# Everything that is not a letter or a digit. Addon labels differ in
# punctuation, spacing and separators between runs -- and the same release
# from the same addon keeps its words.
_SIGNATURE_JUNK_RE = re.compile(r"[^0-9a-z]+")
# Long enough that two different releases cannot collide on it, short enough
# to keep settings.json small.
_SIGNATURE_LIMIT = 120


def stream_signature(name: str, title: str) -> str:
    """A stable key for "this source, from this addon".

    Built from the label because there is nothing else: a stream URL is
    single-use (debrid links expire, and carry a token), so it cannot identify
    the same release tomorrow. Case and punctuation are dropped; the words,
    the resolution and the release group survive.
    """
    text = f"{name} {title}".lower()
    return _SIGNATURE_JUNK_RE.sub(" ", text).strip()[:_SIGNATURE_LIMIT]


def looks_dolby_vision_only(text: str) -> bool:
    """Whether the label reads as Dolby Vision with no base layer named.

    That shape is profile 5 more often than not. It is a guess, and it is
    treated as one everywhere it is used.
    """
    return bool(_DV_RE.search(text)) and not _BASE_LAYER_RE.search(text)


def is_incompatible(stream: Stream, known_bad: frozenset[str] | set[str] = frozenset()) -> bool:
    """Whether this source is one the machine cannot display correctly.

    `known_bad` holds signatures mpv has already reported as profile 5. A
    signature that is NOT in there was either never played or played fine, and
    only then does the label heuristic get a say.
    """
    signature = stream_signature(stream.name, stream.title)
    if signature and signature in known_bad:
        return True
    return looks_dolby_vision_only(f"{stream.name} {stream.title}")


def partition(
    streams: list[Stream], known_bad: frozenset[str] | set[str] = frozenset()
) -> tuple[list[Stream], list[Stream]]:
    """Split into (shown, hidden). Hiding every source is not a service, so a
    list where nothing survives is returned whole -- a magenta picture beats an
    empty page, and the user can see for themselves."""
    shown = [s for s in streams if not is_incompatible(s, known_bad)]
    if not shown:
        return list(streams), []
    hidden = [s for s in streams if is_incompatible(s, known_bad)]
    return shown, hidden


class IncompatibleSources:
    """The toggle, and what the player has learned so far.

    Kept apart from the label heuristic on purpose: `signatures` are verdicts
    mpv handed down, and they survive restarts because meeting the same broken
    release twice is exactly the thing this is meant to prevent.
    """

    def __init__(self) -> None:
        self.enabled: bool = False
        self.signatures: tuple[str, ...] = ()

    def known_bad(self) -> frozenset[str]:
        return frozenset(self.signatures)

    def remember(self, name: str, title: str) -> bool:
        """Record that this source cannot be rendered. Returns whether that was
        news -- the caller persists only when it was."""
        signature = stream_signature(name, title)
        if not signature or signature in self.signatures:
            return False
        self.signatures = (*self.signatures, signature)
        return True
