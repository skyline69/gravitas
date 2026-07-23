"""The software video item's sizing maths (the only part with no Qt scene)."""

from __future__ import annotations

from gravitas.presentation.video.mpv_sw_item import video_geometry


def test_video_fills_an_item_of_the_same_aspect() -> None:
    rect, width, height = video_geometry(1920, 1080, 1.0, 1920, 1080)
    assert (rect.x(), rect.y()) == (0, 0)
    assert (rect.width(), rect.height()) == (1920, 1080)
    assert (width, height) == (1920, 1080)


def test_wider_item_letterboxes_with_pillars() -> None:
    # 16:9 video in a 2:1 item: bars on the left and right, video centered.
    rect, width, height = video_geometry(2000, 1000, 1.0, 1920, 1080)
    assert (rect.width(), rect.height()) == (1777.7777777777778, 1000)
    assert rect.x() == (2000 - rect.width()) / 2
    assert rect.y() == 0
    assert (width, height) == (1778, 1000)


def test_taller_item_letterboxes_with_bars() -> None:
    rect, _, _ = video_geometry(1920, 1200, 1.0, 1920, 1080)
    assert (rect.width(), rect.height()) == (1920, 1080)
    assert rect.x() == 0
    assert rect.y() == 60


def test_buffer_never_exceeds_the_videos_own_resolution() -> None:
    # A 720p file filling a Retina 1440p item: upscaling is the GPU's job, so
    # mpv is still asked for 720p worth of pixels.
    _, width, height = video_geometry(1280, 720, 2.0, 1280, 720)
    assert (width, height) == (1280, 720)


def test_buffer_follows_the_device_pixel_ratio_below_that_cap() -> None:
    # The same 4K file shown in a small Retina item: half the item's logical
    # size in pixels, doubled by the ratio, and well under the source.
    _, width, height = video_geometry(960, 540, 2.0, 3840, 2160)
    assert (width, height) == (1920, 1080)


def test_degenerate_sizes_do_not_divide_by_zero() -> None:
    rect, width, height = video_geometry(0, 0, 2.0, 1920, 1080)
    assert (rect.width(), rect.height()) == (0, 0)
    assert (width, height) == (1, 1)
    # No file loaded yet: mpv reports no display size.
    rect, width, height = video_geometry(800, 600, 1.0, 0, 0)
    assert (rect.width(), rect.height()) == (800, 600)
    assert (width, height) == (1, 1)
