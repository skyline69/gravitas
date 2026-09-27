"""Vendor the format logos the player's badges show (Dolby Vision, HDR10, ...).

The badges name what the playing file is (see track_list.media_format); the
formats that have a mark of their own show it rather than a word. The marks
come from Wikimedia Commons, which records each file's copyright status, and
ship under presentation/qml/formats/ recoloured white for the player's dark
chrome, with where each came from in formats/SOURCES.md. Copyright aside,
every one of them is a trademark of its owner.

    uv run python scripts/vendor_format_logos.py

Formats without a mark (HLG, resolutions, channel layouts) are drawn from the
app's icon font in QML instead.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "gravitas" / "presentation" / "qml" / "formats"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "gravitas-vendor/0.1 (https://github.com/skyline69/gravitas)"}

# (Commons file, name it ships as). The monochrome marks, so the set reads as
# one family on the player.
LOGOS = [
    ("File:Dolby Vision (logo).svg", "dolby-vision.svg"),
    ("File:Dolby Atmos (logo).svg", "dolby-atmos.svg"),
    ("File:HDR 10 logo (black).svg", "hdr10.svg"),
    ("File:HDR 10 plus logo (black).svg", "hdr10-plus.svg"),
    ("File:DTS X B&W.png", "dts-x.png"),
]

# What may ship: marks too simple for copyright, or a free licence whose
# terms SOURCES.md meets (attribution; the recoloured copy under the same
# licence).
ALLOWED = {"Public domain", "CC BY-SA 4.0", "CC BY 4.0", "CC0"}


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def _white_svg(data: bytes) -> bytes:
    """The mark in white: its paths carry no fill of their own, so one on
    the root element colours them all."""
    text = data.decode("utf-8")
    if re.search(r"fill\s*[:=]", text):
        raise SystemExit("an SVG sets its own fills; recolour it by hand")
    return re.sub(r"<svg\b", '<svg fill="#ffffff"', text, count=1).encode("utf-8")


def _white_png(data: bytes) -> bytes:
    """Black on white (or on nothing) to white on nothing: darkness becomes
    opacity."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage.fromData(data).convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            ink = (255 - pixel.lightness()) * pixel.alpha() // 255
            image.setPixelColor(x, y, QColor(255, 255, 255, ink))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(QByteArray(buffer.data()).data())


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    reply = httpx.get(
        API,
        params={
            "action": "query",
            "titles": "|".join(title for title, _ in LOGOS),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "format": "json",
        },
        headers=HEADERS,
        timeout=30,
    ).json()
    info = {page["title"]: page["imageinfo"][0] for page in reply["query"]["pages"].values()}
    sources = [
        "# Format logos",
        "",
        "Fetched by `scripts/vendor_format_logos.py` from Wikimedia Commons and",
        "recoloured white. Each mark is a trademark of its owner.",
        "",
    ]
    for title, name in LOGOS:
        found = info[title]
        meta = found["extmetadata"]
        licence = _plain(meta.get("LicenseShortName", {}).get("value", ""))
        if licence not in ALLOWED:
            raise SystemExit(f"{title}: licence {licence!r} is not one this app may ship")
        data = httpx.get(found["url"], headers=HEADERS, timeout=30, follow_redirects=True)
        data.raise_for_status()
        white = _white_svg(data.content) if name.endswith(".svg") else _white_png(data.content)
        (OUT / name).write_bytes(white)
        author = _plain(meta.get("Artist", {}).get("value", "")) or "unknown"
        page = "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")
        sources.append(f"- `{name}`: [{title}]({page}), by {author}. {licence}.")
        print(f"{name}: {licence}")
    sources.append("")
    (OUT / "SOURCES.md").write_text("\n".join(sources), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
