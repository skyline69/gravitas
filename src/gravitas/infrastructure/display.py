"""What the best connected screen can actually show.

Only the height matters here, and only in physical pixels: a source taller than
the panel is downscaled before anyone sees it, so its extra pixels cost
bandwidth and buy nothing. Qt reports logical sizes, so the device pixel ratio
has to be multiplied back in -- a 1440p panel at 2x reports 1280x720 logical,
and treating that as the cap would demote everything above 720p.

"Best" is the largest screen attached, not the one the window sits on: moving
a window between displays is not a statement about which panel the user
intends to watch on, and re-sorting a list because a window crossed a monitor
boundary would be worse than harmless.
"""

from __future__ import annotations

import logging

from PySide6.QtGui import QGuiApplication

_log = logging.getLogger(__name__)


def best_screen_height() -> int:
    """Physical pixel height of the tallest connected screen, or 0 when that
    cannot be established (no QGuiApplication yet, a headless/offscreen run).
    0 means "do not cap", never "cap at nothing"."""
    app = QGuiApplication.instance()
    if app is None:
        return 0
    heights = []
    for screen in QGuiApplication.screens():
        try:
            heights.append(int(screen.size().height() * screen.devicePixelRatio()))
        except Exception:  # pragma: no cover - defensive: screen torn down mid-query
            continue
    return max(heights, default=0)
