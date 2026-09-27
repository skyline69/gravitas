"""Estimate the connection's usable downstream bandwidth from observed samples.

Samples come from the player's own read speed while something is playing (plus
one probe sample when a bucket has no history at all). Three things make the
raw numbers awkward, and each is answered here rather than at the call site:

* **The player reads at playback rate once its cache is full.** A 25 Mbps file
  on a 500 Mbps line reports ~25 Mbps for most of an episode, and capacity only
  shows in the fast stretches: the start-up fill, a seek, a recovery after a
  stall. Those are a small minority of samples, which rules out an average, a
  median, and in fact any percentile -- a p90 over 40 samples of which 3 are
  fast returns the throttled rate. The estimate is therefore a high-water
  mark, with the single highest sample discarded once there are enough of them
  to discard one (a CDN edge can serve one burst far above the sustained
  rate; it rarely serves two).
* **Networks change.** Samples carry the transport they were taken on, and the
  current session always outranks history once it has enough of its own -- a
  laptop moved from ethernet to a hotel's wifi corrects inside one playback.
* **History goes stale.** Anything older than CONNECTION_SAMPLE_MAX_AGE_S is
  ignored, and never counted as "this bucket has history".
"""

from __future__ import annotations

import time
from collections.abc import Callable

from gravitas.domain.models import (
    CONNECTION_SAMPLE_CAP,
    CONNECTION_SAMPLE_MAX_AGE_S,
    ConnectionSample,
)

# Session samples needed before the session speaks for itself. Below this the
# reading is one cache fill, which says more about the file than the line.
SESSION_QUORUM = 5
# Below this many samples there is nothing to discard: the highest reading is
# all the evidence there is, and refusing it would mean refusing to answer.
_OUTLIER_GUARD_MIN = 4


def high_water_kbps(samples: list[int]) -> int:
    """The fastest rate the link has been seen to sustain.

    Not the fastest sample: from _OUTLIER_GUARD_MIN samples on, the single
    highest is dropped and the next one stands, so one lucky burst cannot
    define the connection. Errs low by construction, which is the safe
    direction -- underestimating puts a smaller file at the top of Sources,
    and a smaller file plays.
    """
    if not samples:
        return 0
    ordered = sorted(samples)
    if len(ordered) < _OUTLIER_GUARD_MIN:
        return ordered[-1]
    return ordered[-2]


def prune(
    samples: tuple[ConnectionSample, ...], *, now: float | None = None
) -> tuple[ConnectionSample, ...]:
    """Drop expired samples, then cap each bucket to its newest CAP entries.

    Capping per bucket, not globally: an evening of wifi playback must not
    evict the ethernet history it never had anything to do with.
    """
    moment = time.time() if now is None else now
    fresh = [s for s in samples if moment - s.at <= CONNECTION_SAMPLE_MAX_AGE_S]
    kept_per_bucket: dict[str, int] = {}
    kept: list[ConnectionSample] = []
    # Walk newest-first so the cap keeps the newest, then restore input order.
    for sample in reversed(fresh):
        seen = kept_per_bucket.get(sample.bucket, 0)
        if seen >= CONNECTION_SAMPLE_CAP:
            continue
        kept_per_bucket[sample.bucket] = seen + 1
        kept.append(sample)
    kept.reverse()
    return tuple(kept)


def has_history(
    samples: tuple[ConnectionSample, ...], bucket: str, *, now: float | None = None
) -> bool:
    """Whether this bucket holds anything worth estimating from. Drives the
    one-shot probe: a bucket with only expired samples is a cold start."""
    moment = time.time() if now is None else now
    return any(s.bucket == bucket and moment - s.at <= CONNECTION_SAMPLE_MAX_AGE_S for s in samples)


def estimate_kbps(
    samples: tuple[ConnectionSample, ...],
    bucket: str,
    session: tuple[ConnectionSample, ...] = (),
    *,
    now: float | None = None,
) -> int | None:
    """Usable downstream bandwidth in kbps, or None when nothing says.

    `session` is what this run of the app has observed. It wins outright once
    it reaches SESSION_QUORUM -- that is the whole answer to "the user moved to
    a slower network"; stored history cannot outvote what is happening now.
    """
    moment = time.time() if now is None else now
    if len(session) >= SESSION_QUORUM:
        return high_water_kbps([s.kbps for s in session]) or None
    fresh = [
        s.kbps
        for s in samples
        if s.bucket == bucket and moment - s.at <= CONNECTION_SAMPLE_MAX_AGE_S
    ]
    if not fresh:
        return None
    return high_water_kbps(fresh) or None


class ConnectionSpeed:
    """The app's live view of the connection: the toggle, the samples, and the
    one estimate everything else reads.

    Session samples are kept apart from persisted history on purpose (see
    estimate_kbps): they are what makes a change of network correct itself
    inside one playback instead of over a week of averages.
    """

    def __init__(self, bucket_provider: Callable[[], str] | None = None) -> None:
        self._bucket_provider = bucket_provider
        self.enabled: bool = False
        self.samples: tuple[ConnectionSample, ...] = ()
        self._session: tuple[ConnectionSample, ...] = ()

    @property
    def bucket(self) -> str:
        if self._bucket_provider is None:
            return "unknown"
        return self._bucket_provider()

    def record(self, kbps: int, *, now: float | None = None) -> None:
        """Add one observation. Zero and negative rates are not observations --
        a paused player and a local file both read as 0 bytes/s."""
        if kbps <= 0:
            return
        moment = int(time.time() if now is None else now)
        sample = ConnectionSample(kbps=kbps, at=moment, bucket=self.bucket)
        self._session = (*self._session, sample)[-CONNECTION_SAMPLE_CAP:]
        self.samples = prune((*self.samples, sample), now=moment)

    def estimate_kbps(self) -> int | None:
        return estimate_kbps(self.samples, self.bucket, self._session)

    def needs_probe(self, *, wanted: bool | None = None) -> bool:
        """Whether a cold-start probe is worth running: someone wants the
        estimate, and this link has told us nothing yet. One probe ends this
        for good -- recording its sample is what makes the answer False
        afterwards.

        `wanted` exists because the sort is no longer the only consumer: the
        source recommendations read the same estimate and are on by default,
        so "is the sort enabled" stopped being the right question. It defaults
        to this object's own toggle, which is what a caller that only cares
        about the sort still means.
        """
        asked = self.enabled if wanted is None else wanted
        return asked and not self._session and not has_history(self.samples, self.bucket)
