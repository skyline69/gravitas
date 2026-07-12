"""MediaPlayer implementation embedding libmpv into a native window (by window id)."""

from __future__ import annotations

import contextlib
import locale
from collections.abc import Callable
from typing import Any

from gravitas.domain.errors import PlaybackFailed

MpvFactory = Callable[[int], Any]


def _default_factory(window_id: int) -> Any:
    # libmpv needs the C numeric locale; Qt may have changed it. Must run
    # right before mpv.MPV() construction (after QGuiApplication init),
    # not at import time.
    locale.setlocale(locale.LC_NUMERIC, "C")

    import mpv  # type: ignore[import-untyped]

    return mpv.MPV(
        wid=str(window_id),
        vo="gpu",
        hwdec="auto-safe",
        osc=False,
        input_default_bindings=False,
    )


class MpvPlayer:
    def __init__(self, window_id: int, factory: MpvFactory = _default_factory) -> None:
        self._tracks_changed_callback: Callable[[], None] | None = None
        try:
            self._mpv = factory(window_id)
        except Exception as exc:  # surface any libmpv init failure uniformly
            raise PlaybackFailed(f"failed to initialise libmpv: {exc}") from exc

    def play(self, url: str) -> None:
        try:
            self._mpv.play(url)
        except Exception as exc:
            raise PlaybackFailed(f"failed to play {url}: {exc}") from exc

    def pause(self) -> None:
        self._mpv["pause"] = True

    def resume(self) -> None:
        self._mpv["pause"] = False

    def seek(self, seconds: float) -> None:
        self._mpv.seek(seconds, reference="absolute")

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        tracks: list[tuple[int, str]] = []
        for track in self._mpv.track_list:
            if track.get("type") == "sub":
                tracks.append((int(track["id"]), str(track.get("title") or f"Track {track['id']}")))
        return tracks

    def set_subtitle_track(self, track_id: int | None) -> None:
        self._mpv["sid"] = "no" if track_id is None else track_id

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._tracks_changed_callback = callback
        mpv = getattr(self, "_mpv", None)
        if mpv is None:
            return
        # python-mpv is untyped and libmpv may not be available in all
        # environments; a failure to register the observer must not
        # crash the player — the callback simply won't fire.
        with contextlib.suppress(Exception):
            mpv.observe_property("track-list", self._on_track_list)

    def _on_track_list(self, _name: str, _value: object) -> None:
        if self._tracks_changed_callback is not None:
            self._tracks_changed_callback()

    def shutdown(self) -> None:
        self._mpv.terminate()
