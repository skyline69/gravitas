"""Regenerate the bundled icon font subset.

Uses classic Material Icons Round (static, filled) — the family the app's
original glyphs came from; the newer variable "Material Symbols" family has
noticeably different (thinner) shapes. Subsets to exactly the glyphs named in
GLYPHS and prints the codepoint for each — keep Icons.qml in sync with that
output.

Run:  uv run --with fonttools python scripts/subset_icons.py
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

# raw.githubusercontent.com directly, not github.com/.../raw/: that path is a
# redirect github.com now refuses to serve to urllib's default User-Agent,
# closing the connection without a response.
REPO = "https://raw.githubusercontent.com/google/material-design-icons/master/font"
FONT_URL = f"{REPO}/MaterialIconsRound-Regular.otf"
CODEPOINTS_URL = f"{REPO}/MaterialIconsRound-Regular.codepoints"

GLYPHS = [
    # navigation / chrome
    "keyboard_arrow_down",
    "arrow_back",
    "play_arrow",
    "pause",
    "search",
    "close",
    "settings",
    "delete",
    "dashboard",
    "theaters",
    "live_tv",
    "local_fire_department",
    # player controls
    "volume_up",
    "volume_off",
    "subtitles",
    "graphic_eq",
    "fullscreen",
    "fullscreen_exit",
    "replay_10",
    "forward_10",
    "fast_rewind",
    "fast_forward",
    "check",
    "picture_in_picture_alt",
    "open_in_full",
    "video_library",
    # watchlist
    "bookmark",
    "bookmark_border",
    "bookmark_add",
    # player format badges (formats without a mark of their own)
    "4k",
    "hd",
    "surround_sound",
]

OUT = Path(__file__).parent.parent / ("src/gravitas/presentation/qml/assets/MaterialSymbols.ttf")


def main() -> int:
    print("downloading codepoints map…")
    with urllib.request.urlopen(CODEPOINTS_URL) as resp:
        pairs = dict(line.split() for line in resp.read().decode().splitlines())
    missing = [g for g in GLYPHS if g not in pairs]
    if missing:
        print(f"unknown glyph names: {missing}", file=sys.stderr)
        return 1
    unicodes = [int(pairs[g], 16) for g in GLYPHS]
    for glyph in GLYPHS:
        print(f"  {glyph} = 0x{pairs[glyph]}")

    print("downloading font…")
    with urllib.request.urlopen(FONT_URL) as resp:
        font = TTFont(io.BytesIO(resp.read()))

    print("subsetting…")
    options = subset.Options()
    options.layout_features = []
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=unicodes)
    subsetter.subset(font)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    font.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
