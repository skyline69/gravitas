"""Deciding which sources this machine cannot display correctly."""

from gravitas.application.compatibility import (
    IncompatibleSources,
    is_incompatible,
    looks_dolby_vision_only,
    partition,
    stream_signature,
)
from gravitas.domain.models import Stream


def _stream(name: str, title: str = "") -> Stream:
    return Stream(name=name, title=title, url="http://h/v.mkv", info_hash=None, file_idx=None)


# Real labels from the source list, both shapes.
_DV_WITH_BASE = "Silo S01E01 ▣ HEVC ✦ DV · HDR10 ♫ Atmos · DD+ ⌗ 5.1 ❖ 11.8 GB · 26.4 Mbps"
_DV_ALONE = "Silo S01E01 ▣ HEVC ✦ DV ♫ Atmos · DD+ ⌗ 5.1 ❖ 11.7 GB · 26.4 Mbps"


def test_dolby_vision_over_an_hdr10_base_is_fine():
    # Profile 8.1: the base layer is what any renderer falls back to.
    assert looks_dolby_vision_only(_DV_WITH_BASE) is False


def test_dolby_vision_with_no_base_layer_is_suspect():
    assert looks_dolby_vision_only(_DV_ALONE) is True


def test_a_source_without_dolby_vision_is_never_suspect():
    assert looks_dolby_vision_only("Silo S01E01 HEVC · HDR10 · Atmos") is False
    assert looks_dolby_vision_only("Mayday 2026 1080p WEB x264") is False


def test_hlg_and_plain_sdr_also_count_as_a_base_layer():
    assert looks_dolby_vision_only("Film DV · HLG") is False
    assert looks_dolby_vision_only("Film DV · SDR") is False


def test_signatures_ignore_punctuation_and_case():
    assert stream_signature("Silo S01E01 ✦ DV", "") == stream_signature("silo  s01e01 - dv", "")


def test_signatures_separate_different_releases():
    assert stream_signature("Silo S01E01 FLUX", "") != stream_signature("Silo S01E01 NTb", "")


def test_a_learned_verdict_hides_a_label_the_heuristic_would_clear():
    # Profile 5 that the label did not betray: only playback could know.
    stream = _stream(_DV_WITH_BASE)
    assert is_incompatible(stream) is False
    known = {stream_signature(_DV_WITH_BASE, "")}
    assert is_incompatible(stream, known) is True


def test_partition_splits_shown_from_hidden():
    good, bad = _stream(_DV_WITH_BASE), _stream(_DV_ALONE)
    shown, hidden = partition([good, bad])
    assert shown == [good]
    assert hidden == [bad]


def test_partition_keeps_order_within_each_side():
    a, b, c = _stream(_DV_ALONE, "a"), _stream(_DV_WITH_BASE, "b"), _stream(_DV_ALONE, "c")
    shown, hidden = partition([a, b, c])
    assert [s.title for s in shown] == ["b"]
    assert [s.title for s in hidden] == ["a", "c"]


def test_hiding_everything_hides_nothing():
    # An empty source list helps no one: a magenta picture beats no picture,
    # and the user can judge it themselves.
    streams = [_stream(_DV_ALONE, "a"), _stream(_DV_ALONE, "b")]
    shown, hidden = partition(streams)
    assert shown == streams
    assert hidden == []


def test_remembering_is_idempotent():
    store = IncompatibleSources()
    assert store.remember("Silo S01E01 DV", "") is True
    assert store.remember("Silo  s01e01 · dv", "") is False  # same release, other punctuation
    assert len(store.signatures) == 1


def test_an_empty_label_is_not_remembered():
    store = IncompatibleSources()
    assert store.remember("", "") is False
    assert store.signatures == ()
