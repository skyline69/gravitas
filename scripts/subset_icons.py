"""Regenerate the bundled Material Symbols icon font subset.

Downloads the Material Symbols Rounded variable font, pins it to the filled
style (FILL=1, wght=400, GRAD=0, opsz=24), and subsets it to exactly the
glyphs named in GLYPHS. Keep GLYPHS in sync with Icons.qml.

Run:  uv run --with fonttools python scripts/subset_icons.py
"""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

REPO = "https://github.com/google/material-design-icons/raw/master/variablefont"
FONT_URL = f"{REPO}/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf"
CODEPOINTS_URL = f"{REPO}/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.codepoints"

GLYPHS = [
    # existing set (Icons.qml)
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
]

OUT = Path(__file__).parent.parent / (
    "src/gravitas/presentation/qml/assets/MaterialSymbols.ttf"
)


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

    print("downloading variable font…")
    with urllib.request.urlopen(FONT_URL) as resp:
        font = TTFont(io.BytesIO(resp.read()))

    print("instancing FILL=1 wght=400 GRAD=0 opsz=24…")
    instantiateVariableFont(
        font, {"FILL": 1, "wght": 400, "GRAD": 0, "opsz": 24}, inplace=True
    )

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
