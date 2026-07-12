"""QObject bridge exposing playback controls to QML."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.ports import MediaPlayer


class PlayerController(QObject):
    errorOccurred = Signal(str)

    def __init__(self, player: MediaPlayer) -> None:
        super().__init__()
        self._player = player

    @Slot(str)
    def play(self, url: str) -> None:
        try:
            self._player.play(url)
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))

    @Slot()
    def pause(self) -> None:
        self._player.pause()

    @Slot()
    def resume(self) -> None:
        self._player.resume()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        self._player.seek(seconds)

    @Slot(int)
    def selectSubtitle(self, track_id: int) -> None:
        self._player.set_subtitle_track(None if track_id < 0 else track_id)

    @Slot(result="QVariantList")
    def subtitleTracks(self) -> list[dict[str, object]]:
        return [{"id": tid, "title": title} for tid, title in self._player.subtitle_tracks()]
