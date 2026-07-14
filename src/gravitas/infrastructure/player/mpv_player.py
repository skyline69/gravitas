"""MediaPlayer implementation on libmpv, rendered in-scene via the render API.

No `wid` embedding: foreign-window embedding is dead on Wayland. The mpv
instance is created with vo=libmpv and drawn into the Qt scene by the
presentation layer's MpvVideoItem through render_handle().
"""

from __future__ import annotations

import contextlib
import locale
import os
import sys
from collections import Counter
from collections.abc import Callable, MutableMapping
from typing import Any

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import SubtitleStyle

MpvFactory = Callable[[], Any]

# Where Homebrew puts libmpv.dylib (Apple Silicon, then Intel).
_MACOS_LIBMPV_DIRS = ("/opt/homebrew/lib", "/usr/local/lib")
# dyld's built-in fallback list — overridden (not extended) the moment the
# variable is set, so it must be re-included explicitly.
_MACOS_DYLD_DEFAULTS = (os.path.expanduser("~/lib"), "/usr/local/lib", "/lib", "/usr/lib")


def _ensure_libmpv_discoverable(
    environ: MutableMapping[str, str] = os.environ, platform: str = sys.platform
) -> None:
    """macOS: python-mpv locates libmpv with ctypes.util.find_library, which
    searches DYLD_FALLBACK_LIBRARY_PATH — Homebrew's prefix is not in it on
    Apple Silicon, so the import fails with libmpv installed. ctypes reads the
    environment at lookup time, so extending it here (in-process) works."""
    if platform != "darwin":
        return
    existing = [p for p in environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":") if p]
    if not existing:
        existing = list(_MACOS_DYLD_DEFAULTS)
    for candidate in _MACOS_LIBMPV_DIRS:
        if candidate not in existing:
            existing.insert(0, candidate)
    environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(existing)


def _default_factory() -> Any:
    # libmpv needs the C numeric locale; Qt may have changed it. Must run
    # right before mpv.MPV() construction (after QGuiApplication init),
    # not at import time.
    locale.setlocale(locale.LC_NUMERIC, "C")

    _ensure_libmpv_discoverable()

    import mpv  # type: ignore[import-untyped]

    return mpv.MPV(
        vo="libmpv",
        hwdec="auto-safe",
        osc=False,
        input_default_bindings=False,
        keep_open="yes",  # hold the last frame instead of tearing the surface down
    )


# ISO 639-1 and common 639-2 codes -> display names. Containers carry either.
_LANG_NAMES = {
    "en": "English",
    "eng": "English",
    "de": "German",
    "ger": "German",
    "deu": "German",
    "fr": "French",
    "fre": "French",
    "fra": "French",
    "es": "Spanish",
    "spa": "Spanish",
    "it": "Italian",
    "ita": "Italian",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "ja": "Japanese",
    "jpn": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "zh": "Chinese",
    "chi": "Chinese",
    "zho": "Chinese",
    "ar": "Arabic",
    "ara": "Arabic",
    "hi": "Hindi",
    "hin": "Hindi",
    "tr": "Turkish",
    "tur": "Turkish",
    "pl": "Polish",
    "pol": "Polish",
    "nl": "Dutch",
    "dut": "Dutch",
    "nld": "Dutch",
    "sv": "Swedish",
    "swe": "Swedish",
    "no": "Norwegian",
    "nor": "Norwegian",
    "da": "Danish",
    "dan": "Danish",
    "fi": "Finnish",
    "fin": "Finnish",
    "cs": "Czech",
    "cze": "Czech",
    "ces": "Czech",
    "el": "Greek",
    "gre": "Greek",
    "ell": "Greek",
    "he": "Hebrew",
    "heb": "Hebrew",
    "hu": "Hungarian",
    "hun": "Hungarian",
    "ro": "Romanian",
    "rum": "Romanian",
    "ron": "Romanian",
    "uk": "Ukrainian",
    "ukr": "Ukrainian",
    "th": "Thai",
    "tha": "Thai",
    "vi": "Vietnamese",
    "vie": "Vietnamese",
    "id": "Indonesian",
    "ind": "Indonesian",
    "fa": "Persian",
    "per": "Persian",
    "fas": "Persian",
    "bg": "Bulgarian",
    "bul": "Bulgarian",
    "hr": "Croatian",
    "hrv": "Croatian",
    "sr": "Serbian",
    "srp": "Serbian",
    "sk": "Slovak",
    "slo": "Slovak",
    "slk": "Slovak",
    "sl": "Slovenian",
    "slv": "Slovenian",
    "lt": "Lithuanian",
    "lit": "Lithuanian",
    "lv": "Latvian",
    "lav": "Latvian",
    "et": "Estonian",
    "est": "Estonian",
    "ca": "Catalan",
    "cat": "Catalan",
    "ms": "Malay",
    "may": "Malay",
    "msa": "Malay",
    "ta": "Tamil",
    "tam": "Tamil",
    "te": "Telugu",
    "tel": "Telugu",
}

_CHANNEL_LABELS = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}


def _lang_name(code: object) -> str:
    """'en' -> 'English', 'fr-CA' -> 'French (CA)'; unknown codes pass through."""
    if not isinstance(code, str) or not code:
        return ""
    base, _, region = code.partition("-")
    name = _LANG_NAMES.get(base.lower())
    if name is None:
        return code
    return f"{name} ({region.upper()})" if region else name


def _track_label(track: dict[str, Any]) -> str:
    lang = _lang_name(track.get("lang"))
    title = str(track.get("title") or "")
    if title and lang and lang.split(" (")[0].lower() in title.lower():
        # The title already names the language ("English (United States)") —
        # prefixing the code again is noise.
        parts = [title]
    elif title and lang:
        parts = [lang, title]
    elif title or lang:
        parts = [title or lang]
    else:
        parts = [f"Track {track['id']}"]
    if track.get("type") == "audio":
        count = track.get("demux-channel-count")
        if isinstance(count, int) and count:
            channels = _CHANNEL_LABELS.get(count, f"{count}ch")
            if channels.lower() not in " ".join(parts).lower():
                parts.append(channels)
    label = " · ".join(parts)
    flags = [
        name
        for key, name in (
            ("forced", "Forced"),
            ("hearing-impaired", "SDH"),
            ("visual-impaired", "AD"),
        )
        if track.get(key)
    ]
    if flags:
        label += " — " + ", ".join(flags)
    return label


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

    def current_subtitle_track(self) -> int | None:
        return self._current_track("sid")

    def current_audio_track(self) -> int | None:
        return self._current_track("aid")

    def apply_subtitle_style(self, style: SubtitleStyle) -> None:
        self._mpv.sub_font_size = style.font_size
        self._mpv.sub_color = style.color
        self._mpv.sub_border_size = style.border_size
        # mpv colors are #AARRGGBB; opacity % -> alpha byte on black.
        alpha = round(style.back_opacity * 255 / 100)
        self._mpv.sub_back_color = f"#{alpha:02X}000000"
        self._mpv.sub_bold = style.bold

    def _current_track(self, prop: str) -> int | None:
        # mpv returns an int id, or False/"no"/None when disabled.
        value = self._prop(prop)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _tracks(self, kind: str) -> list[tuple[int, str]]:
        tracks: list[tuple[int, str]] = []
        for track in self._mpv.track_list:
            if track.get("type") == kind:
                tracks.append((int(track["id"]), _track_label(track)))
        # Identically-labelled tracks (same language, no distinguishing
        # metadata) get an index so the menu rows aren't interchangeable.
        counts = Counter(label for _, label in tracks)
        seen: Counter[str] = Counter()
        deduped: list[tuple[int, str]] = []
        for tid, label in tracks:
            if counts[label] > 1:
                seen[label] += 1
                deduped.append((tid, f"{label} · #{seen[label]}"))
            else:
                deduped.append((tid, label))
        return deduped

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
