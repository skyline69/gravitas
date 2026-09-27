"""Vendor the flag images the language menus show.

The menus used to show flag emoji, which depend on the system having a colour
emoji font: the Flatpak runtime has none, and Windows has no flag glyphs at
all, so the flags simply were not there. The images ship with the app instead,
under presentation/qml/flags/, one SVG per country the catalog in
domain/languages.py names -- only those, not all 270.

Source: lipis/flag-icons (MIT), pinned below, via jsDelivr's npm mirror. The
licence is copied alongside. globe.svg (for languages with no country) is our
own and is left alone.

    uv run python scripts/vendor_flags.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gravitas.domain.languages import LANGUAGES  # noqa: E402

VERSION = "7.5.0"
BASE = f"https://cdn.jsdelivr.net/npm/flag-icons@{VERSION}"
OUT = ROOT / "src" / "gravitas" / "presentation" / "qml" / "flags"


def main() -> None:
    countries = sorted({language.country.lower() for language in LANGUAGES if language.country})
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = {f"{country}.svg" for country in countries}
    # Drop flags no language uses any more, so the folder stays the catalog's.
    for stale in OUT.glob("*.svg"):
        if stale.name not in wanted and stale.name != "globe.svg":
            stale.unlink()
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for country in countries:
            response = client.get(f"{BASE}/flags/4x3/{country}.svg")
            response.raise_for_status()
            (OUT / f"{country}.svg").write_bytes(response.content)
        licence = client.get(f"{BASE}/LICENSE")
        licence.raise_for_status()
        (OUT / "LICENSE-flag-icons").write_text(
            f"Flags from lipis/flag-icons {VERSION}, https://github.com/lipis/flag-icons\n\n"
            + licence.text,
            encoding="utf-8",
        )
    total = sum(path.stat().st_size for path in OUT.glob("*.svg"))
    print(f"vendored {len(countries)} flags ({total / 1024:.0f} KiB) into {OUT}")


if __name__ == "__main__":
    main()
