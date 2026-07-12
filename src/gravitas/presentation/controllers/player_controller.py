"""QObject bridge exposing playback controls to QML."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.ports import MediaPlayer


class PlayerController(QObject):
    errorOccurred = Signal(str)

    def __init__(self, player_factory: Callable[[], MediaPlayer]) -> None:
        super().__init__()
        self._factory = player_factory
        self._player: MediaPlayer | None = None

    def _ensure(self) -> MediaPlayer | None:
        if self._player is None:
            try:
                self._player = self._factory()
            except PlaybackFailed as exc:
                self.errorOccurred.emit(str(exc))
                return None
        return self._player

    @Slot(str)
    def play(self, url: str) -> None:
        player = self._ensure()
        if player is None:
            return
        try:
            player.play(url)
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))

    @Slot()
    def pause(self) -> None:
        if self._player is None:
            return
        self._player.pause()

    @Slot()
    def resume(self) -> None:
        if self._player is None:
            return
        self._player.resume()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        if self._player is None:
            return
        self._player.seek(seconds)

    @Slot(int)
    def selectSubtitle(self, track_id: int) -> None:
        if self._player is None:
            return
        self._player.set_subtitle_track(None if track_id < 0 else track_id)

    @Slot(result="QVariantList")
    def subtitleTracks(self) -> list[dict[str, object]]:
        if self._player is None:
            return []
        return [{"id": tid, "title": title} for tid, title in self._player.subtitle_tracks()]
