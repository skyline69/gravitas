"""Ordering the Sources list against a measured connection."""

from gravitas.application.stream_ranking import (
    HEADROOM,
    delivered_height,
    estimate_bitrate_mbps,
    is_oversized,
    parse_bitrate_mbps,
    parse_resolution,
    parse_runtime_seconds,
    rank_streams,
)
from gravitas.domain.models import Stream


def _stream(name: str, title: str = "") -> Stream:
    return Stream(name=name, title=title, url="http://h/v.mkv", info_hash=None, file_idx=None)


# Labels as addons actually write them (these are real Sources rows).
_REAL_LABEL = (
    "Silo S01E01 ▣ HEVC ✦ DV · HDR10 ♫ Atmos · DD+ ⌗ 5.1 ❖ 11.4 GB/22.8 GB · "
    "25.6 Mbps ⛨ [TB] STorz · FLUX ⚑ EN · SUB (EN · DE) » WEB T₁ APPLE TV+ 6385"
)


def test_parses_stated_bitrate_out_of_a_real_label():
    assert parse_bitrate_mbps(_REAL_LABEL) == 25.6


def test_size_becomes_a_bitrate_only_when_the_runtime_is_known():
    label = "Movie ❖ 4.5 GB · [TB] STorz"
    assert parse_bitrate_mbps(label) is None
    # 4.5 GB over 60 minutes is 10 Mbps.
    assert parse_bitrate_mbps(label, runtime_s=3600) == 10.0


def test_comma_decimals_are_read_as_decimals():
    assert parse_bitrate_mbps("Film · 11,5 Mbps") == 11.5


def test_stated_rate_wins_over_size():
    label = "Film ❖ 20 GB · 6 Mbps"
    assert parse_bitrate_mbps(label, runtime_s=3600) == 6.0


def test_resolution_words_map_to_heights():
    assert parse_resolution("4K ⟨Web-dl⟩") == 2160
    assert parse_resolution("Silo 1080p WEB") == 1080
    assert parse_resolution("no resolution here") == 0


def test_unknown_bitrate_falls_back_to_the_resolution_nominal():
    assert estimate_bitrate_mbps(_stream("4K ⟨Web-dl⟩")) == 25.0
    assert estimate_bitrate_mbps(_stream("720p ⟨Web-dl⟩")) == 4.0
    # Nothing recognisable at all still produces a number to compare.
    assert estimate_bitrate_mbps(_stream("mystery release")) > 0


def test_runtime_strings():
    assert parse_runtime_seconds("102 min") == 6120.0
    assert parse_runtime_seconds("2h 15m") == 8100.0
    assert parse_runtime_seconds("") is None
    assert parse_runtime_seconds("unknown") is None


def test_no_estimate_leaves_the_addon_order_untouched():
    streams = [_stream("720p · 4 Mbps"), _stream("4K · 60 Mbps"), _stream("1080p · 8 Mbps")]
    ranked, over = rank_streams(streams, None)
    assert [s.name for s in ranked] == [s.name for s in streams]
    # Nothing is marked against a bandwidth nobody measured.
    assert over == (False, False, False)


def test_streams_that_fit_come_first_best_quality_at_the_top():
    streams = [
        _stream("1080p · 8 Mbps"),
        _stream("4K · 60 Mbps"),
        _stream("4K · 20 Mbps"),
        _stream("720p · 4 Mbps"),
    ]
    ranked, over = rank_streams(streams, 30_000)  # 30 Mbps line, 24 Mbps budget
    assert [s.name for s in ranked] == [
        "4K · 20 Mbps",
        "1080p · 8 Mbps",
        "720p · 4 Mbps",
        "4K · 60 Mbps",
    ]
    assert over == (False, False, False, True)


def test_nothing_is_dropped_when_every_source_is_too_heavy():
    streams = [_stream("4K · 60 Mbps"), _stream("4K · 80 Mbps")]
    ranked, over = rank_streams(streams, 5_000)
    assert len(ranked) == 2
    assert over == (True, True)
    # Still best-first within the band.
    assert ranked[0].name == "4K · 80 Mbps"


def test_ties_keep_the_addon_order():
    first = _stream("1080p · 8 Mbps", "first")
    second = _stream("1080p · 8 Mbps", "second")
    ranked, _ = rank_streams([first, second], 100_000)
    assert [s.title for s in ranked] == ["first", "second"]


def test_the_budget_carries_headroom():
    # Exactly at the line: the stream counts as over budget, because the
    # budget is HEADROOM of what was measured, not all of it.
    at_line = _stream(f"4K · {10 * HEADROOM + 0.1:.1f} Mbps")
    _, over = rank_streams([at_line], 10_000)
    assert over == (True,)


def test_a_source_ranks_by_the_picture_the_screen_gets():
    # 1440p panel: the 4K and the 1440p both deliver 1440p, so the one that
    # does it without downscaling -- and without the extra bandwidth -- leads.
    streams = [
        _stream("4K · 25 Mbps"),
        _stream("1080p · 8 Mbps"),
        _stream("1440p · 14 Mbps"),
        _stream("720p · 4 Mbps"),
    ]
    ranked, _ = rank_streams(streams, 100_000, display_height=1440)
    assert [s.name for s in ranked] == [
        "1440p · 14 Mbps",
        "4K · 25 Mbps",
        "1080p · 8 Mbps",
        "720p · 4 Mbps",
    ]


def test_oversized_sources_still_outrank_smaller_ones():
    # Being taller than the panel is not a fault: a 4K still delivers more
    # than a 1080p on a 1440p screen.
    ranked, _ = rank_streams(
        [_stream("1080p · 8 Mbps"), _stream("4K · 25 Mbps")], 100_000, display_height=1440
    )
    assert [s.name for s in ranked] == ["4K · 25 Mbps", "1080p · 8 Mbps"]


def test_two_native_sources_still_rank_by_bitrate():
    ranked, _ = rank_streams(
        [_stream("1080p · 6 Mbps"), _stream("1080p · 12 Mbps")], 100_000, display_height=1440
    )
    assert [s.name for s in ranked] == ["1080p · 12 Mbps", "1080p · 6 Mbps"]


def test_two_oversized_sources_still_rank_by_bitrate():
    ranked, _ = rank_streams(
        [_stream("4K · 18 Mbps"), _stream("4K · 25 Mbps")], 100_000, display_height=1080
    )
    assert [s.name for s in ranked] == ["4K · 25 Mbps", "4K · 18 Mbps"]


def test_an_unknown_screen_caps_nothing():
    # Qt could not answer (headless, no screens): ordering must fall back to
    # the label's own resolution, never to a guessed panel.
    streams = [_stream("1080p · 8 Mbps"), _stream("4K · 25 Mbps")]
    ranked, _ = rank_streams(streams, 100_000, display_height=0)
    assert [s.name for s in ranked] == ["4K · 25 Mbps", "1080p · 8 Mbps"]
    assert [is_oversized(s, 0) for s in ranked] == [False, False]


def test_delivered_height_is_capped_by_the_panel():
    assert delivered_height(_stream("4K"), 1080) == 1080
    assert delivered_height(_stream("720p"), 1080) == 720
    assert delivered_height(_stream("4K"), 0) == 2160
    # Nothing recognisable stays 0 and sorts last, capped or not.
    assert delivered_height(_stream("mystery"), 1080) == 0


def test_oversized_marks_only_what_the_screen_cannot_show():
    assert is_oversized(_stream("4K"), 1080) is True
    assert is_oversized(_stream("1080p"), 1080) is False
    assert is_oversized(_stream("4K"), 2160) is False


def test_bandwidth_still_outranks_the_screen():
    # A source that fits the panel perfectly but cannot be streamed still
    # belongs under one that plays: the band is the outer key.
    streams = [_stream("1440p · 90 Mbps"), _stream("720p · 4 Mbps")]
    ranked, over = rank_streams(streams, 20_000, display_height=1440)
    assert [s.name for s in ranked] == ["720p · 4 Mbps", "1440p · 90 Mbps"]
    assert over == (False, True)
