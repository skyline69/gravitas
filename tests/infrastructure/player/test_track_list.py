"""What a file is, read from its tracks the way both engines report them."""

from __future__ import annotations

from typing import Any

from gravitas.domain.models import MediaFormat
from gravitas.infrastructure.player.track_list import media_format


def _video(**fields: Any) -> dict[str, Any]:
    return {"type": "video", "id": 1, "selected": True, **fields}


def _audio(**fields: Any) -> dict[str, Any]:
    return {"type": "audio", "id": 1, "selected": True, **fields}


def test_nothing_open_is_no_format() -> None:
    assert media_format([]) == MediaFormat()


def test_dolby_vision_wins_over_its_hdr10_base_layer() -> None:
    # Profile 8 carries an HDR10 base layer: the file is still Dolby Vision.
    fmt = media_format([_video(**{"dolby-vision-profile": 8, "color-transfer": "pq"})])
    assert fmt.hdr == "Dolby Vision"
    assert fmt.dolby_vision_profile == 8


def test_the_transfer_names_hdr10_hdr10_plus_and_hlg() -> None:
    assert media_format([_video(**{"color-transfer": "pq"})]).hdr == "HDR10"
    hdr10_plus = _video(**{"color-transfer": "pq", "hdr10-plus": True})
    assert media_format([hdr10_plus]).hdr == "HDR10+"
    assert media_format([_video(**{"color-transfer": "hlg"})]).hdr == "HLG"
    assert media_format([_video()]).hdr == ""


def test_resolution_follows_whichever_side_says_more() -> None:
    def res(w: int, h: int) -> str:
        return media_format([_video(**{"demux-w": w, "demux-h": h})]).resolution

    assert res(3840, 2160) == "4K"
    assert res(3840, 1600) == "4K", "a scope film in 4K"
    assert res(1440, 1080) == "1080p", "a 4:3 film in 1080p"
    assert res(1920, 800) == "1080p"
    assert res(1280, 720) == "720p"
    assert res(720, 576) == ""


def test_immersive_audio_and_channels_come_from_the_selected_track() -> None:
    tracks = [
        _audio(id=1, selected=False, **{"demux-channel-count": 2, "codec-profile": "LC"}),
        _audio(
            id=2,
            **{"demux-channel-count": 8, "codec-profile": "Dolby TrueHD + Dolby Atmos"},
        ),
    ]
    fmt = media_format(tracks)
    assert fmt.immersive_audio == "Dolby Atmos"
    assert fmt.channels == "7.1"
    dts = _audio(**{"demux-channel-count": 6, "codec-profile": "DTS-HD MA + DTS:X"})
    assert media_format([dts]) == MediaFormat(immersive_audio="DTS:X", channels="5.1")


def test_stereo_is_not_worth_a_badge() -> None:
    assert media_format([_audio(**{"demux-channel-count": 2})]).channels == ""
