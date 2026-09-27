"""Turning raw playback read-speeds into one usable bandwidth estimate."""

import time

from gravitas.application.connection_speed import (
    SESSION_QUORUM,
    ConnectionSpeed,
    estimate_kbps,
    has_history,
    high_water_kbps,
    prune,
)
from gravitas.domain.models import (
    CONNECTION_SAMPLE_CAP,
    CONNECTION_SAMPLE_MAX_AGE_S,
    ConnectionSample,
)

NOW = 1_700_000_000


def _sample(kbps: int, *, age_s: int = 0, bucket: str = "wifi") -> ConnectionSample:
    return ConnectionSample(kbps=kbps, at=NOW - age_s, bucket=bucket)


def test_high_water_discards_the_single_fastest_burst():
    # One CDN burst does not define the connection; the next-fastest does.
    assert high_water_kbps([10_000, 12_000, 11_000, 900_000]) == 12_000


def test_high_water_keeps_the_top_sample_while_there_are_too_few():
    assert high_water_kbps([10_000, 40_000]) == 40_000


def test_high_water_of_nothing_is_zero():
    assert high_water_kbps([]) == 0


def test_estimate_reads_the_fast_stretches_not_the_bulk():
    # What playback looks like: a couple of fast cache fills, then reads
    # throttled to the file's own bitrate. The line is 100 Mbps; every
    # average, median and percentile over this would answer 8.
    samples = (*(_sample(8_000) for _ in range(38)), _sample(99_000), _sample(100_000))
    assert estimate_kbps(samples, "wifi", now=NOW) == 99_000


def test_stale_samples_are_ignored():
    old = (_sample(50_000, age_s=CONNECTION_SAMPLE_MAX_AGE_S + 60),)
    assert estimate_kbps(old, "wifi", now=NOW) is None
    assert has_history(old, "wifi", now=NOW) is False


def test_a_bucket_never_answers_for_another():
    ethernet = (_sample(500_000, bucket="ethernet"),)
    assert estimate_kbps(ethernet, "wifi", now=NOW) is None
    assert estimate_kbps(ethernet, "ethernet", now=NOW) == 500_000


def test_the_current_session_outvotes_stored_history():
    # Stored: a week of fiber. Now: a hotel's wifi, five samples in.
    history = tuple(_sample(500_000, age_s=3600) for _ in range(20))
    session = tuple(_sample(12_000) for _ in range(SESSION_QUORUM))
    assert estimate_kbps(history, "wifi", session, now=NOW) == 12_000


def test_a_short_session_does_not_outvote_history():
    history = tuple(_sample(500_000, age_s=3600) for _ in range(20))
    session = tuple(_sample(12_000) for _ in range(SESSION_QUORUM - 1))
    assert estimate_kbps(history, "wifi", session, now=NOW) == 500_000


def test_prune_caps_each_bucket_separately():
    wifi = [_sample(i + 1, bucket="wifi") for i in range(CONNECTION_SAMPLE_CAP + 10)]
    ethernet = [_sample(999, bucket="ethernet")]
    kept = prune(tuple(wifi + ethernet), now=NOW)
    assert sum(1 for s in kept if s.bucket == "wifi") == CONNECTION_SAMPLE_CAP
    # An evening of wifi does not evict the ethernet history.
    assert sum(1 for s in kept if s.bucket == "ethernet") == 1
    # The newest wifi samples are the ones kept.
    assert kept[-2].kbps == CONNECTION_SAMPLE_CAP + 10


def test_prune_drops_expired_samples():
    kept = prune((_sample(1, age_s=CONNECTION_SAMPLE_MAX_AGE_S + 1), _sample(2)), now=NOW)
    assert [s.kbps for s in kept] == [2]


def test_tracker_records_against_the_current_bucket():
    bucket = "wifi"
    speed = ConnectionSpeed(lambda: bucket)
    speed.record(30_000, now=NOW)
    assert speed.samples[-1].bucket == "wifi"
    bucket = "cellular"
    speed.record(4_000, now=NOW)
    assert speed.samples[-1].bucket == "cellular"


def test_tracker_discards_non_observations():
    speed = ConnectionSpeed()
    speed.record(0)
    speed.record(-5)
    assert speed.samples == ()
    assert speed.estimate_kbps() is None


def test_probe_is_wanted_only_while_the_link_is_unmeasured():
    speed = ConnectionSpeed(lambda: "wifi")
    assert speed.needs_probe() is False  # feature off
    speed.enabled = True
    assert speed.needs_probe() is True
    speed.record(30_000)
    assert speed.needs_probe() is False


def test_expired_history_still_counts_as_a_cold_start():
    speed = ConnectionSpeed(lambda: "wifi")
    speed.enabled = True
    speed.samples = (
        ConnectionSample(
            kbps=50_000,
            at=int(time.time()) - CONNECTION_SAMPLE_MAX_AGE_S - 60,
            bucket="wifi",
        ),
    )
    assert speed.needs_probe() is True
