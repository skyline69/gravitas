"""Software video sizing maths (geometry.py), and the mpv software item's resize latch."""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, QRectF, QTimer

from gravitas.presentation.video.geometry import video_geometry
from gravitas.presentation.video.mpv_sw_item import MpvSwVideoItem


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
    # The rect is exact; the buffer behind it steps up to the next multiple of
    # 64 (1778 -> 1792) with its height derived to keep the video's aspect.
    assert (width, height) == (1792, 1008)


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


def test_buffer_size_only_moves_in_steps() -> None:
    """A drag changes the item size every frame. mpv rebuilds its scaler on
    every change of sw_size -- measured at ~4.6 ms a frame against ~0.7 ms at a
    size it has already seen -- so the buffer must not follow every pixel."""
    sizes = {video_geometry(w, w * 9 / 16, 1.0, 1920, 1080)[1] for w in range(800, 1000)}
    assert sizes == {832, 896, 960, 1024}


def test_buffer_is_never_smaller_than_the_rect_it_is_stretched_over() -> None:
    # Rounding up, not to nearest: a buffer below the drawn size would be
    # upscaled by the GPU and read as a soft picture.
    for w in range(300, 1600, 7):
        rect, buffer_w, _ = video_geometry(w, w * 9 / 16, 2.0, 1920, 1080)
        assert buffer_w >= min(1920, rect.width() * 2.0)


def test_stepped_buffer_keeps_the_videos_aspect() -> None:
    # Off-aspect buffer dimensions make mpv letterboxes INSIDE the buffer, and
    # those bars are then stretched over the rect as part of the picture.
    for w in range(400, 1400, 11):
        _, buffer_w, buffer_h = video_geometry(w, w * 9 / 16, 1.0, 1920, 1080)
        assert abs(buffer_w / buffer_h - 1920 / 1080) < 0.01


def test_step_is_capped_by_the_source_resolution() -> None:
    # Stepping up must never ask mpv for more pixels than the file has.
    _, width, height = video_geometry(1910, 1074, 1.0, 1920, 1080)
    assert (width, height) == (1920, 1080)


def _spin(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_resizing_latches_while_the_item_changes_size(qapp: object) -> None:
    """Rendering video through a resize is what makes a drag stutter: measured
    on an M2, frames arrive at ~32/s with gaps to 249 ms while it does, and at
    ~65/s with none over 20 ms while it does not. updatePaintNode consults this
    latch to keep the frame it has instead."""
    item = MpvSwVideoItem()
    assert item._resizing is False

    item.geometryChange(QRectF(0, 0, 640, 360), QRectF(0, 0, 480, 270))
    assert item._resizing is True

    # ...and it lets go on its own, or a paused video would never be redrawn.
    _spin(400)
    assert item._resizing is False


def test_moving_without_resizing_does_not_latch(qapp: object) -> None:
    # Same size at a new position: nothing to re-render, nothing to freeze.
    item = MpvSwVideoItem()
    item.geometryChange(QRectF(40, 40, 480, 270), QRectF(0, 0, 480, 270))
    assert item._resizing is False
