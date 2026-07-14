"""QObject bridge exposing playback state and controls to QML."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Property, QObject, Signal, Slot

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import SubtitleStyle
from gravitas.domain.ports import MediaPlayer


class PlayerController(QObject):
    errorOccurred = Signal(str)
    subtitleTracksChanged = Signal()
    stateChanged = Signal()

    def __init__(
        self,
        player_factory: Callable[[], MediaPlayer],
        style_provider: Callable[[], SubtitleStyle] | None = None,
    ) -> None:
        super().__init__()
        self._factory = player_factory
        self._style_provider = style_provider
        self._player: MediaPlayer | None = None

    def _ensure(self) -> MediaPlayer | None:
        if self._player is None:
            try:
                self._player = self._factory()
            except PlaybackFailed as exc:
                self.errorOccurred.emit(str(exc))
                return None
            self._player.set_tracks_changed_callback(self._on_tracks_changed)
            self._player.set_state_changed_callback(self._on_state_changed)
            self.applySubtitleStyle()
        return self._player

    def _on_tracks_changed(self) -> None:
        # May be invoked from mpv's own thread; Signal.emit() is safe to call
        # from any thread and is delivered to QML via a queued connection.
        self.subtitleTracksChanged.emit()

    def _on_state_changed(self) -> None:
        self.stateChanged.emit()

    # --- playback state (notify: stateChanged) ---

    @Property(bool, notify=stateChanged)
    def paused(self) -> bool:
        return self._player.is_paused() if self._player is not None else False

    @Property(float, notify=stateChanged)
    def duration(self) -> float:
        return self._player.duration() if self._player is not None else 0.0

    @Property(float, notify=stateChanged)
    def volume(self) -> float:
        return self._player.volume() if self._player is not None else 100.0

    @Property(bool, notify=stateChanged)
    def muted(self) -> bool:
        return self._player.is_muted() if self._player is not None else False

    @Slot(result=float)
    def position(self) -> float:
        """Polled by a QML Timer while the player page is open — cheaper than
        observing time-pos, which fires many times a second."""
        return self._player.position() if self._player is not None else 0.0

    @Slot(result=bool)
    def isLoading(self) -> bool:
        """Polled with position: buffering or mid-seek."""
        return self._player.is_loading() if self._player is not None else False

    # --- renderer bridge ---

    @Slot(QObject)
    def attachVideo(self, item: QObject) -> None:
        """Hand the opaque mpv handle to the in-scene video item."""
        player = self._ensure()
        if player is None:
            return
        item.setProperty("handle", player.render_handle())

    # --- controls ---

    @Slot(str)
    def play(self, url: str) -> None:
        player = self._ensure()
        if player is None:
            return
        try:
            player.play(url)
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))
        self.stateChanged.emit()

    @Slot()
    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    @Slot()
    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()

    @Slot()
    def resume(self) -> None:
        if self._player is not None:
            self._player.resume()

    @Slot()
    def togglePause(self) -> None:
        if self._player is None:
            return
        if self._player.is_paused():
            self._player.resume()
        else:
            self._player.pause()
        self.stateChanged.emit()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        if self._player is None:
            return
        self._player.seek(seconds)

    @Slot(float)
    def seekBy(self, delta: float) -> None:
        if self._player is None:
            return
        target = max(0.0, self._player.position() + delta)
        duration = self._player.duration()
        if duration > 0:
            target = min(target, duration)
        self._player.seek(target)

    @Slot(float)
    def setVolume(self, volume: float) -> None:
        if self._player is None:
            return
        self._player.set_volume(volume)
        self.stateChanged.emit()

    @Slot()
    def toggleMute(self) -> None:
        if self._player is None:
            return
        self._player.set_muted(not self._player.is_muted())
        self.stateChanged.emit()

    # --- tracks ---

    @Slot()
    def applySubtitleStyle(self) -> None:
        """Push the user's subtitle style to the player (live if playing)."""
        if self._player is None or self._style_provider is None:
            return
        self._player.apply_subtitle_style(self._style_provider())

    @Slot(int)
    def selectSubtitle(self, track_id: int) -> None:
        if self._player is None:
            return
        self._player.set_subtitle_track(None if track_id < 0 else track_id)

    @Slot(int)
    def selectAudio(self, track_id: int) -> None:
        if self._player is None:
            return
        self._player.set_audio_track(None if track_id < 0 else track_id)

    @Slot(result=int)
    def currentSubtitle(self) -> int:
        if self._player is None:
            return -1
        current = self._player.current_subtitle_track()
        return current if current is not None else -1

    @Slot(result=int)
    def currentAudio(self) -> int:
        if self._player is None:
            return -1
        current = self._player.current_audio_track()
        return current if current is not None else -1

    @Slot(result="QVariantList")
    def subtitleTracks(self) -> list[dict[str, object]]:
        if self._player is None:
            return []
        return [{"id": tid, "title": title} for tid, title in self._player.subtitle_tracks()]

    @Slot(result="QVariantList")
    def audioTracks(self) -> list[dict[str, object]]:
        if self._player is None:
            return []
        return [{"id": tid, "title": title} for tid, title in self._player.audio_tracks()]
