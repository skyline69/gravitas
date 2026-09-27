"""Bake packaging/icon/gravitas.svg into a multi-size Windows .ico.

Run after editing the SVG:  uv run python scripts/build_ico.py

Windows picks a different size for each surface -- 16px in the title bar, 32px
on the taskbar, 256px in the "large icons" view -- and scales whatever it finds
when the exact size is missing, which is where icons turn to mush. So every
size it asks for is rendered from the vector, at the size it will be shown.

Each entry is stored as a PNG inside the container (the format Windows has
accepted since Vista, and the only one that keeps 256px files small). Qt's own
.ico writer takes a single image only, hence the hand-rolled directory.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QSize
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "icon" / "gravitas.svg"
TARGET = ROOT / "packaging" / "icon" / "gravitas.ico"

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()

    # QBuffer's own internal byte array: handing it an external QByteArray
    # stores a pointer Python is free to collect out from under it.
    buffer = QBuffer()
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def main() -> int:
    # Rendering an SVG needs a QGuiApplication, not a display. The app object
    # must outlive every QImage made under it, hence the name kept to the end.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication([sys.argv[0]])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"cannot parse {SOURCE}")
        return 1

    frames = [render(renderer, size) for size in SIZES]

    # ICONDIR: reserved, type 1 (icon), image count.
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    directory = b""
    for size, frame in zip(SIZES, frames, strict=True):
        # 256 is written as 0: the field is a single byte.
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(frame), offset
        )
        offset += len(frame)

    TARGET.write_bytes(header + directory + b"".join(frames))
    app.quit()
    print(f"wrote {TARGET} ({TARGET.stat().st_size / 1024:.0f} KB, sizes {list(SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
