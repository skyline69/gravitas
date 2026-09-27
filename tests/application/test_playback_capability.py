"""What playback teaches about this machine's decoding."""

from gravitas.application.playback_capability import (
    PlaybackCapability,
    bucket_key,
    codec_family,
    drops_per_minute,
    height_tier,
)
from gravitas.domain.models import DECODE_OBSERVATION_MIN_S, DecodeReport


def _report(codec: str = "hevc", height: int = 2160, dropped: int = 0) -> DecodeReport:
    return DecodeReport(codec=codec, height=height, dropped_frames=dropped)


def test_codec_families_cover_both_vocabularies():
    # mpv's descriptive string, and the release scene's names.
    assert codec_family("HEVC (High Efficiency Video Coding)") == "hevc"
    assert codec_family("Silo S01E01 ▣ x265 · HDR10") == "hevc"
    assert codec_family("Movie 2160p AV1 WEB") == "av1"
    assert codec_family("Movie 1080p x264") == "h264"


def test_a_label_naming_no_codec_is_not_filed_under_one():
    assert codec_family("Silo S01E01 ⟨Web-dl⟩ 11.4 GB") == ""
    assert bucket_key("", 2160) == ""


def test_height_rounds_down_to_the_tier_it_reaches():
    assert height_tier(2160) == 2160
    # A letterboxed 4K frame is 3840x1600; it is still a 4K decode.
    assert height_tier(1600) == 1440
    assert height_tier(1080) == 1080
    assert height_tier(200) == 0


def test_bucket_needs_both_halves():
    assert bucket_key("hevc", 2160) == "hevc:2160"
    assert bucket_key("hevc", 0) == ""


def test_a_short_playback_says_nothing():
    # 1200 drops in 30 seconds is a seek settling, not a verdict.
    assert drops_per_minute(1200, 30.0) == 0.0
    capability = PlaybackCapability()
    assert capability.observe(_report(dropped=1200), 30.0) is False
    assert capability.strained == ()


def test_sustained_drops_convict_the_bucket_and_report_the_change():
    capability = PlaybackCapability()
    # 600 drops over 10 minutes is 60/min, past DECODE_STRAIN_DPM.
    assert capability.observe(_report(codec="AV1", height=2160, dropped=600), 600.0) is True
    assert capability.strained == ("av1:2160",)
    assert capability.is_strained("av1", 2160) is True
    # The same verdict again is not news, so nothing is persisted for it.
    assert capability.observe(_report(codec="AV1", height=2160, dropped=600), 600.0) is False


def test_a_verdict_is_scoped_to_its_own_codec_and_size():
    capability = PlaybackCapability()
    capability.observe(_report(codec="AV1", height=2160, dropped=600), 600.0)
    assert capability.is_strained("hevc", 2160) is False
    assert capability.is_strained("av1", 1080) is False


def test_a_clean_playback_clears_a_bucket_that_was_strained():
    capability = PlaybackCapability()
    capability.strained = ("hevc:2160",)
    # 20 drops over 10 minutes is 2/min, under DECODE_SMOOTH_DPM.
    assert capability.observe(_report(codec="hevc", height=2160, dropped=20), 600.0) is True
    assert capability.strained == ()


def test_the_gap_between_the_thresholds_holds_the_verdict_still():
    # 200 drops over 10 minutes is 20/min: neither strained nor smooth.
    marginal = _report(codec="hevc", height=2160, dropped=200)
    clean = PlaybackCapability()
    assert clean.observe(marginal, 600.0) is False
    assert clean.strained == ()

    convicted = PlaybackCapability()
    convicted.strained = ("hevc:2160",)
    assert convicted.observe(marginal, 600.0) is False
    assert convicted.strained == ("hevc:2160",)


def test_the_observation_floor_is_the_documented_one():
    capability = PlaybackCapability()
    just_under = DECODE_OBSERVATION_MIN_S - 1
    assert capability.observe(_report(dropped=10_000), just_under) is False
    assert capability.observe(_report(dropped=10_000), DECODE_OBSERVATION_MIN_S) is True
