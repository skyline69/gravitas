"""Where a software-rendered picture sits in its item, and how big to render it.

Shared by every video item that renders on the CPU and uploads a texture:
mpv's software render path (mpv_sw_item.py) and the native engine's
(native_item.py). Each rendered pixel is CPU work there, which is what every
rule below is about.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF

# Buffer widths are rounded up to a multiple of this many device pixels.
#
# Every change of the buffer size makes the renderer rebuild its scaler, and
# that dominates: on an M2, mpv rendering 1080p into a buffer whose size
# changes each frame costs ~4.6 ms against ~0.7 ms at a size it has already
# seen. A window drag changes
# the size on every single frame, which is exactly why resizing felt heavy --
# the steady-state cost was never the problem. Snapping to a step turns a drag
# into a handful of reconfigurations instead of one per pixel, and the cost
# goes back to the constant-size figure.
#
# Rounding UP means the buffer is never smaller than the rect it is stretched
# over, so this trades at most 63 columns of extra CPU work for the smoothness
# and never softens the picture.
BUFFER_QUANTUM = 64

# How long after the last geometry change the item waits before rendering
# video again. Long enough to cover the gap between two frames of a drag,
# short enough that letting go feels instant.
RESIZE_SETTLE_MS = 150


def video_geometry(
    item_width: float,
    item_height: float,
    dpr: float,
    video_width: int,
    video_height: int,
) -> tuple[QRectF, int, int]:
    """Where the video sits inside the item, and how big a buffer to render into.

    Returns (rect in item coordinates, buffer width, buffer height in device
    pixels). The rect letterboxes the video's aspect inside the item -- mpv's
    OpenGL path does that inside the FBO, but here every rendered pixel is
    CPU work, so the bars are better left undrawn and the node simply made
    smaller than the item.

    The buffer never exceeds the video's own resolution: upscaling is work the
    GPU does for free when the texture is stretched over a larger rect, and
    paying for it on the CPU buys nothing. It is capped to the item as well,
    so a 4K file in a small window renders small -- but only in steps of
    BUFFER_QUANTUM, because changing the size at all is the expensive part.

    The rect itself is never quantized: the video still fills its letterbox to
    the pixel, at whatever source resolution the step landed on.
    """
    if item_width <= 0 or item_height <= 0 or video_width <= 0 or video_height <= 0:
        return QRectF(0, 0, max(item_width, 0.0), max(item_height, 0.0)), 1, 1
    scale = min(item_width / video_width, item_height / video_height)
    fitted_w = video_width * scale
    fitted_h = video_height * scale
    rect = QRectF(
        (item_width - fitted_w) / 2.0,
        (item_height - fitted_h) / 2.0,
        fitted_w,
        fitted_h,
    )
    wanted_w = fitted_w * dpr
    stepped_w = math.ceil(wanted_w / BUFFER_QUANTUM) * BUFFER_QUANTUM
    buffer_w = max(1, min(video_width, stepped_w))
    # Derived from the width rather than stepped in its own right: the two must
    # keep the video's aspect, or mpv letterboxes a second time inside the
    # buffer and the picture comes back with bars baked into it.
    buffer_h = max(1, min(video_height, round(buffer_w * video_height / video_width)))
    return rect, buffer_w, buffer_h


def fitted_rect(item_width: float, item_height: float, video_w: int, video_h: int) -> QRectF:
    """The video's rect inside the item, preserving aspect (letterboxed).

    For a picture rendered on the GPU at the video's own resolution (mpv's
    Vulkan item, the native engine's zero-copy path): there is no buffer size
    to choose, only where to put the quad.
    """
    if item_width <= 0 or item_height <= 0 or video_w <= 0 or video_h <= 0:
        return QRectF(0, 0, max(item_width, 0.0), max(item_height, 0.0))
    scale = min(item_width / video_w, item_height / video_h)
    width = video_w * scale
    height = video_h * scale
    return QRectF((item_width - width) / 2.0, (item_height - height) / 2.0, width, height)
