"""Regenerate qml/assets/corner-frame*.png.

The frame is PosterCard's rounded-corner trick: a BorderImage whose corners
are filled with the page background (Theme.bg) and whose centre is
transparent, drawn over the unmasked poster. One shared texture instead of a
per-card OpacityMask shader keeps the whole grid in a single scene-graph
batch. Re-run this if Theme.bg or the 14px poster radius ever changes:

    QT_QPA_PLATFORM=offscreen uv run python scripts/gen_corner_frame.py
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath

BG = QColor("#141414")  # Theme.bg in components/Theme.qml
RADIUS = 14  # PosterCard's poster rounding, in 1x pixels
ASSETS = Path(__file__).resolve().parent.parent / "src/gravitas/presentation/qml/assets"


def frame(size: int, radius: float, path: Path) -> None:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(BG)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    rounded = QPainterPath()
    rounded.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.fillPath(rounded, Qt.GlobalColor.black)
    painter.end()
    if not img.save(str(path)):
        raise SystemExit(f"could not write {path}")
    print(f"wrote {path}")


def main() -> None:
    QGuiApplication([])
    side = RADIUS * 2 + 2  # corners plus a 2px stretchable middle
    frame(side, RADIUS, ASSETS / "corner-frame.png")
    frame(side * 2, RADIUS * 2, ASSETS / "corner-frame@2x.png")


if __name__ == "__main__":
    main()
