"""QObject bridge exposing playback state and controls to QML."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import MediaType, SubtitleStyle
from gravitas.domain.ports import MediaPlayer
from gravitas.logging_setup import abbreviate_url

_log = logging.getLogger(__name__)


class PlayerController(QObject):
    errorOccurred = Signal(str)
    subtitleTracksChanged = Signal()
    stateChanged = Signal()
    mediaContextChanged = Signal()
    resumed = Signal(float)
    progressRecorded = Signal()
    # One playback lifecycle event: action ("start" | "pause" | "stop"), the
    # media context, position and duration at that moment. main.py wires it
    # to the Trakt scrobbler; with nothing connected it is inert.
    scrobbleEvent = Signal(str, "QVariantMap", float, float)  # type: ignore[arg-type]

    # Frequent enough that a hard kill costs seconds, not minutes; rare enough
    # that a two-hour film writes ~1400 rows' worth of UPSERTs, not 7 million.
    SAVE_INTERVAL_MS = 5000

    def __init__(
        self,
        player_factory: Callable[[], MediaPlayer],
        style_provider: Callable[[], SubtitleStyle] | None = None,
        progress: WatchProgressRepository | None = None,
    ) -> None:
        super().__init__()
        self._factory = player_factory
        self._style_provider = style_provider
        self._progress = progress
        self._player: MediaPlayer | None = None
        self._context: dict[str, str] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(PlayerController.SAVE_INTERVAL_MS)
        self._save_timer.timeout.connect(self._on_tick)

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

    @Property(str, notify=mediaContextChanged)
    def mediaTitle(self) -> str:
        """What is playing, for the player's own chrome. Empty for playback
        with no identity (trailers), which hides the overlay."""
        return self._context.get("name", "")

    @Property(str, notify=mediaContextChanged)
    def mediaLabel(self) -> str:
        """The episode line under the title ("S1E3 · Red Tide"); empty for
        movies."""
        return self._context.get("label", "")

    @Slot(result=float)
    def position(self) -> float:
        """Polled by a QML Timer while the player page is open — cheaper than
        observing time-pos, which fires many times a second."""
        return self._player.position() if self._player is not None else 0.0

    @Slot(result=bool)
    def isLoading(self) -> bool:
        """Polled with position: buffering or mid-seek."""
        return self._player.is_loading() if self._player is not None else False

    @Slot(result=float)
    def bufferedTo(self) -> float:
        """Polled with position: how far ahead (absolute seconds) the demuxer
        has downloaded — the timeline's loaded track."""
        return self._player.buffered_to() if self._player is not None else 0.0

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
    @Slot(str, "QVariantMap")
    def play(self, url: str, headers: dict[str, str] | None = None) -> None:
        player = self._ensure()
        if player is None:
            return
        start = self._resume_position()
        title = self._context.get("name") or "?"
        label = self._context.get("label") or ""
        _log.info(
            "playing %s%s%s",
            title,
            f" — {label}" if label else "",
            f" (resuming at {start:.0f}s)" if start > 0 else "",
        )
        try:
            # behaviorHints.proxyHeaders.request from the chosen stream: some
            # addons 403 without their Referer/User-Agent.
            player.play(
                url,
                start=start,
                headers=tuple((str(k), str(v)) for k, v in (headers or {}).items()),
            )
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))
            return
        self._emit_scrobble("start")
        if start > 0:
            # StackView applies the `url` property (which triggers this play())
            # between beginCreate() and completeCreate(); Player.qml's
            # Connections { target: playerController } only becomes live in
            # completeCreate(). Emitting synchronously here fires into the
            # void. Defer to the next event-loop turn so the page is fully
            # constructed — and its Connections live — before resumed fires.
            QTimer.singleShot(0, lambda: self.resumed.emit(start))
        self._save_timer.start()
        self.stateChanged.emit()

    @Slot(str)
    def openExternal(self, url: str) -> None:
        """Hand an externalUrl stream to the system browser.

        The protocol's externalUrl points at a web page (a service's own
        player), not a media file -- mpv cannot do anything with it.
        """
        if url:
            _log.info("opening external stream in browser: %s", abbreviate_url(url))
            QDesktopServices.openUrl(QUrl(url))

    @Slot()
    def stop(self) -> None:
        if self._player is not None:
            _log.info(
                "stopped %s at %.0f/%.0fs",
                self._context.get("name") or "?",
                self._player.position(),
                self._player.duration(),
            )
            self._record()
            # Before player.stop(): position dies with the playback.
            self._emit_scrobble("stop")
            self._save_timer.stop()
            self._player.stop()

    @Slot()
    def pause(self) -> None:
        if self._player is not None:
            _log.debug("paused at %.0fs", self._player.position())
            self._player.pause()
            self._record()
            self._emit_scrobble("pause")

    @Slot()
    def resume(self) -> None:
        if self._player is not None:
            _log.debug("resumed at %.0fs", self._player.position())
            self._player.resume()
            self._emit_scrobble("start")

    @Slot()
    def togglePause(self) -> None:
        if self._player is None:
            return
        if self._player.is_paused():
            self._player.resume()
            self._emit_scrobble("start")
        else:
            self._player.pause()
            self._emit_scrobble("pause")
        self.stateChanged.emit()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        if self._player is None:
            return
        _log.debug("seek to %.0fs", seconds)
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

    # --- watch progress ---

    @Slot("QVariantMap")
    def setMediaContext(self, context: dict[str, object]) -> None:
        """Identify what is about to play. QML calls this immediately before
        play(url); without it nothing is recorded and nothing resumes.

        Keys: mediaId, videoId, type, name, poster, label.
        """
        self._context = {
            str(key): "" if value is None else str(value) for key, value in context.items()
        }
        self.mediaContextChanged.emit()

    @Slot()
    def flushProgress(self) -> None:
        """Record now. Wired to aboutToQuit so the last seconds survive."""
        self._record()

    def is_recording(self) -> bool:
        """True while the autosave timer is live. Not a Slot — QML has no use
        for it; it exists so a test can assert the timer's lifecycle without
        waiting out a real interval."""
        return self._save_timer.isActive()

    def _on_tick(self) -> None:
        if self._player is not None and not self._player.is_paused():
            self._record()

    def _emit_scrobble(self, action: str) -> None:
        """Snapshot the moment: consumers run async, and by the time they
        look, the player may have moved on (or been stopped)."""
        if not self._context.get("mediaId") or self._player is None:
            return
        self.scrobbleEvent.emit(
            action, dict(self._context), self._player.position(), self._player.duration()
        )

    def _resume_position(self) -> float:
        media_id = self._context.get("mediaId", "")
        if self._progress is None or not media_id:
            return 0.0
        return self._progress.resume_position(media_id, self._context.get("videoId", ""))

    def _record(self) -> None:
        media_id = self._context.get("mediaId", "")
        if self._progress is None or self._player is None or not media_id:
            return
        duration = self._player.duration()
        if duration <= 0:
            # mpv has not parsed the file yet; a fraction against 0 is noise.
            return
        media_type: MediaType = "series" if self._context.get("type") == "series" else "movie"
        self._progress.record(
            media_id=media_id,
            video_id=self._context.get("videoId", ""),
            type=media_type,
            name=self._context.get("name", ""),
            poster=self._context.get("poster") or None,
            label=self._context.get("label", ""),
            position=self._player.position(),
            duration=duration,
        )
        self.progressRecorded.emit()

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
