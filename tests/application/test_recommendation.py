"""Which sources earn a Recommended mark, and which cannot."""

from gravitas.application.compatibility import stream_signature
from gravitas.application.recommendation import (
    MAX_PICKS,
    is_instant,
    recommend,
    tier_label,
    useful_mbps,
)
from gravitas.domain.models import Stream


def _stream(name: str, title: str = "", *, url: str | None = "http://h/v.mkv") -> Stream:
    return Stream(name=name, title=title, url=url, info_hash=None, file_idx=None)


def _indices(picks) -> list[int]:
    return [p.index for p in picks]


def test_one_pick_per_quality_the_screen_can_show():
    streams = [
        _stream("4K ⟨Web-dl⟩ · 20 Mbps · heavier"),
        _stream("4K ⟨Web-dl⟩ · 24 Mbps · best 4K"),
        _stream("1080p ⟨Web-dl⟩ · 6 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps · best 1080p"),
        _stream("720p ⟨Web-dl⟩ · 3 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=2160)
    # Best tier first, one row each, and the best encode inside each tier.
    assert _indices(picks) == [1, 3, 4]
    assert "4K" in picks[0].reason
    assert "1080p" in picks[1].reason


def test_nothing_is_marked_when_no_source_names_its_quality():
    streams = [_stream("Silo S01E01 ⟨Web-dl⟩ ⛨ [TB] STorz")]
    assert recommend(streams, kbps=100_000) == ()


def test_a_source_over_the_measured_ceiling_is_not_recommended():
    streams = [
        _stream("4K ⟨Remux⟩ · 80 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 6 Mbps"),
    ]
    # 20 Mbps line: HEADROOM puts the ceiling at 16 Mbps.
    picks = recommend(streams, kbps=20_000, display_height=2160)
    assert _indices(picks) == [1]


def test_without_a_measurement_the_ceiling_is_the_tier_nominal():
    remux = _stream("4K ⟨Remux⟩ · 80 Mbps")
    web = _stream("4K ⟨Web-dl⟩ · 24 Mbps")
    picks = recommend([remux, web], kbps=None, display_height=2160)
    assert _indices(picks) == [1]
    assert "hasn't been measured" in picks[0].reason


def test_a_cached_source_outranks_a_better_encode_that_has_to_be_fetched():
    streams = [
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
        _stream("4K ⚡ ⟨Web-dl⟩ · 20 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=2160)
    assert _indices(picks) == [1]
    assert "starts instantly" in picks[0].reason


def test_at_the_same_delivered_picture_the_native_source_wins():
    # On a 1080p panel both deliver 1080p; the 4K one pays four times over.
    streams = [
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=1080)
    assert _indices(picks)[0] == 1


def test_one_tier_still_offers_the_cheaper_choice():
    # A 1080p panel collapses these into one tier, so "best" is the only
    # choice left — unless something delivers the same picture for less.
    streams = [
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 12 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 3 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=1080)
    assert _indices(picks) == [1, 2]
    assert "Same picture for less" in picks[1].reason


def test_a_near_identical_bitrate_is_not_offered_as_an_alternative():
    streams = [
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 7.5 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=1080)
    assert _indices(picks) == [0]


def test_a_source_the_renderer_cannot_display_is_never_recommended():
    # Bare DV with no base layer: the profile 5 shape (see compatibility.py).
    streams = [
        _stream("4K ▣ HEVC ✦ DV · 24 Mbps"),
        _stream("4K ▣ HEVC ✦ DV · HDR10 · 20 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=2160)
    assert _indices(picks) == [1]


def test_a_release_remembered_as_unplayable_is_never_recommended():
    broken = _stream("4K ▣ HEVC · HDR10 · 24 Mbps ⛨ FLUX")
    streams = [broken, _stream("4K ▣ HEVC · HDR10 · 20 Mbps ⛨ NTb")]
    known_bad = frozenset({stream_signature(broken.name, broken.title)})
    picks = recommend(streams, kbps=100_000, display_height=2160, known_bad=known_bad)
    assert _indices(picks) == [1]


def test_a_codec_this_machine_drops_frames_on_is_not_recommended():
    streams = [
        _stream("4K ▣ AV1 · 24 Mbps"),
        _stream("4K ▣ HEVC · HDR10 · 20 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=2160, strained=frozenset({"av1:2160"}))
    assert _indices(picks) == [1]


def test_the_strain_verdict_is_about_the_file_not_the_panel():
    # A 4K AV1 file still decodes as 4K AV1 on a 1080p screen: the downscale
    # happens after the decoder, so the verdict still applies.
    streams = [_stream("4K ▣ AV1 · 20 Mbps"), _stream("1080p ▣ HEVC · 8 Mbps")]
    picks = recommend(streams, kbps=100_000, display_height=1080, strained=frozenset({"av1:2160"}))
    assert _indices(picks) == [1]


def test_an_external_only_source_cannot_be_recommended():
    web_page = Stream(
        name="1080p ⟨Web⟩",
        title="",
        url=None,
        info_hash=None,
        file_idx=None,
        external_url="https://example.test/watch",
    )
    assert recommend([web_page], kbps=100_000, display_height=1080) == ()


def test_the_number_of_marks_is_capped():
    streams = [
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
        _stream("1440p ⟨Web-dl⟩ · 12 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps"),
        _stream("720p ⟨Web-dl⟩ · 4 Mbps"),
        _stream("480p ⟨Web-dl⟩ · 1 Mbps"),
    ]
    picks = recommend(streams, kbps=100_000, display_height=2160)
    assert len(picks) == MAX_PICKS
    # The cap takes the best tiers, not the first rows.
    assert _indices(picks) == [0, 1, 2]


def test_a_measured_reason_names_the_measurement():
    picks = recommend([_stream("1080p · 8 Mbps")], kbps=50_000, display_height=1080)
    assert "50 Mbps" in picks[0].reason


def test_tier_and_instant_helpers():
    assert tier_label(2160) == "4K"
    assert tier_label(1080) == "1080p"
    assert is_instant(_stream("4K ⚡ ⟨Web-dl⟩")) is True
    assert is_instant(_stream("4K ⟨Web-dl⟩ Cached")) is True
    assert is_instant(_stream("4K ⟨Web-dl⟩")) is False


def test_an_empty_list_and_a_zero_limit_mark_nothing():
    assert recommend([], kbps=100_000) == ()
    assert recommend([_stream("1080p · 8 Mbps")], kbps=100_000, limit=0) == ()


def test_bitrate_stops_counting_once_the_panel_cannot_show_it():
    # Every source is 4K and the panel is a 14" MacBook's 1964 physical
    # pixels, so all of them are downscaled to the same picture. Paying 25
    # Mbps for it is how a recommendation ends up buffering.
    streams = [
        _stream("4K ⚡ ▣ HEVC · HDR10+ · 25.3 Mbps"),
        _stream("4K ⚡ ▣ HEVC · HDR10+ · 20 Mbps"),
        _stream("4K ⚡ ▣ HEVC · HDR10+ · 14 Mbps"),
        _stream("4K ⚡ ▣ HEVC · HDR10+ · 6 Mbps"),
    ]
    picks = recommend(streams, kbps=200_000, display_height=1964)
    # 14 Mbps saturates what 1440p shows; 6 Mbps does not, 20 and 25.3 only
    # spend more for the identical frame.
    assert picks[0].index == 2


def test_an_odd_panel_height_is_not_treated_as_an_unknown_screen():
    # 1964 is not a row in NOMINAL_MBPS. Looking it up directly falls back to
    # the "unknown" rate, which is below 1440p's and would make an unusual
    # panel the strictest screen in the table.
    assert useful_mbps(1964, 100.0) == useful_mbps(1440, 100.0)
    assert useful_mbps(1964, 100.0) > useful_mbps(0, 100.0)


def test_a_source_that_cannot_saturate_the_tier_still_loses_to_one_that_can():
    streams = [
        _stream("4K ⟨Web-dl⟩ · 4 Mbps"),
        _stream("4K ⟨Web-dl⟩ · 12 Mbps"),
    ]
    picks = recommend(streams, kbps=200_000, display_height=2160)
    assert picks[0].index == 1
