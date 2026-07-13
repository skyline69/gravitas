"""MediaPlayer implementation on libmpv, rendered in-scene via the render API.

No `wid` embedding: foreign-window embedding is dead on Wayland. The mpv
instance is created with vo=libmpv and drawn into the Qt scene by the
presentation layer's MpvVideoItem through render_handle().
"""

from __future__ import annotations

import contextlib
import locale
from collections.abc import Callable
from typing import Any

from gravitas.domain.errors import PlaybackFailed

MpvFactory = Callable[[], Any]


def _default_factory() -> Any:
    # libmpv needs the C numeric locale; Qt may have changed it. Must run
    # right before mpv.MPV() construction (after QGuiApplication init),
    # not at import time.
    locale.setlocale(locale.LC_NUMERIC, "C")

    import mpv  # type: ignore[import-untyped]

    return mpv.MPV(
        vo="libmpv",
        hwdec="auto-safe",
        osc=False,
        input_default_bindings=False,
        keep_open="yes",  # hold the last frame instead of tearing the surface down
    )


def _track_label(track: dict[str, Any]) -> str:
    lang = track.get("lang")
    title = track.get("title")
    if lang and title:
        return f"{lang} · {title}"
    return str(title or lang or f"Track {track['id']}")


class MpvPlayer:
    def __init__(self, factory: MpvFactory = _default_factory) -> None:
        self._tracks_changed_callback: Callable[[], None] | None = None
        self._state_changed_callback: Callable[[], None] | None = None
        self._track_observer_registered = False
        self._state_observers_registered = False
        try:
            self._mpv = factory()
        except Exception as exc:  # surface any libmpv init failure uniformly
            raise PlaybackFailed(f"failed to initialise libmpv: {exc}") from exc

    # --- playback ---

    def play(self, url: str) -> None:
        try:
            self._mpv.play(url)
            self._mpv.pause = False
        except Exception as exc:
            raise PlaybackFailed(f"failed to play {url}: {exc}") from exc

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._mpv.command("stop")

    def pause(self) -> None:
        self._mpv.pause = True

    def resume(self) -> None:
        self._mpv.pause = False

    def is_paused(self) -> bool:
        return bool(self._prop("pause"))

    def seek(self, seconds: float) -> None:
        # Absolute seeks fail harmlessly before the file is loaded.
        with contextlib.suppress(Exception):
            self._mpv.seek(seconds, reference="absolute")

    def position(self) -> float:
        value = self._prop("time_pos")
        return float(value) if value is not None else 0.0

    def duration(self) -> float:
        value = self._prop("duration")
        return float(value) if value is not None else 0.0

    # --- audio ---

    def set_volume(self, volume: float) -> None:
        self._mpv.volume = max(0.0, min(100.0, volume))

    def volume(self) -> float:
        value = self._prop("volume")
        return float(value) if value is not None else 100.0

    def set_muted(self, muted: bool) -> None:
        self._mpv.mute = muted

    def is_muted(self) -> bool:
        return bool(self._prop("mute"))

    def is_loading(self) -> bool:
        return bool(self._prop("paused_for_cache")) or bool(self._prop("seeking"))

    # --- tracks ---

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return self._tracks("sub")

    def set_subtitle_track(self, track_id: int | None) -> None:
        self._mpv.sid = "no" if track_id is None else track_id

    def audio_tracks(self) -> list[tuple[int, str]]:
        return self._tracks("audio")

    def set_audio_track(self, track_id: int | None) -> None:
        self._mpv.aid = "no" if track_id is None else track_id

    def _tracks(self, kind: str) -> list[tuple[int, str]]:
        tracks: list[tuple[int, str]] = []
        for track in self._mpv.track_list:
            if track.get("type") == kind:
                tracks.append((int(track["id"]), _track_label(track)))
        return tracks

    # --- callbacks / renderer ---

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._tracks_changed_callback = callback
        if callback is None:
            # Leave any existing observer registered -- unobserve_property is
            # fragile across python-mpv/libmpv versions, and _on_track_list
            # is a no-op once the stored callback is cleared, so this is
            # harmless.
            return
        if self._track_observer_registered:
            return
        # python-mpv is untyped and libmpv may not be available in all
        # environments; a failure to register the observer must not
        # crash the player — the callback simply won't fire.
        with contextlib.suppress(Exception):
            self._mpv.observe_property("track-list", self._on_track_list)
            self._track_observer_registered = True

    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None:
        self._state_changed_callback = callback
        if callback is None or self._state_observers_registered:
            return
        with contextlib.suppress(Exception):
            self._mpv.observe_property("pause", self._on_state)
            self._mpv.observe_property("duration", self._on_state)
            self._mpv.observe_property("mute", self._on_state)
            self._state_observers_registered = True

    def render_handle(self) -> object | None:
        handle: object = self._mpv
        return handle

    def _on_track_list(self, _name: str, _value: object) -> None:
        if self._tracks_changed_callback is not None:
            self._tracks_changed_callback()

    def _on_state(self, _name: str, _value: object) -> None:
        if self._state_changed_callback is not None:
            self._state_changed_callback()

    def _prop(self, name: str) -> Any:
        # Attribute access reads mpv PROPERTIES; dict-style access on the MPV
        # object reads the options/ namespace, where runtime state like
        # time_pos does not exist.
        try:
            return getattr(self._mpv, name)
        except Exception:
            return None

    def shutdown(self) -> None:
        self._mpv.terminate()
