"""QObject bridge exposing playback state and controls to QML."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, Protocol

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from gravitas.application.link_warmup import LinkWarmup
from gravitas.application.segments import classify, sections
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain import languages
from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import (
    DecodeReport,
    MediaFormat,
    MediaType,
    Segments,
    Subtitle,
    SubtitleStyle,
    TrackLanguages,
)
from gravitas.domain.ports import IdleInhibitor, MediaPlayer, SegmentSource, StreamAccelerator
from gravitas.logging_setup import abbreviate_url
from gravitas.presentation.external_url import open_in_browser

_log = logging.getLogger(__name__)


# Words in a release name that say nothing about which release it is.
_COMMON_RELEASE_WORDS = frozenset(
    {"the", "and", "mkv", "mp4", "srt", "vtt", "sub", "subs", "english", "eng", "season"}
)


def _release_words(text: str) -> set[str]:
    """The distinctive words of a release name ("1080p", "web", "phr0sty"),
    for telling which subtitle file was made for the release playing. The
    episode marker is dropped: every file for the episode shares it."""
    words = {w for w in re.split(r"[^0-9a-z]+", text.lower()) if len(w) >= 3}
    return {w for w in words if w not in _COMMON_RELEASE_WORDS and not re.fullmatch(r"s\d+e\d+", w)}


class _SpeedSink(Protocol):
    """Where observed download rates go (ConnectionSpeed satisfies it)."""

    def record(self, kbps: int) -> None: ...


class _IncompatibleSink(Protocol):
    """Where "this source cannot be rendered" verdicts go
    (IncompatibleSources satisfies it). remember() reports whether the verdict
    was new, so a repeat play writes nothing."""

    def remember(self, name: str, title: str) -> bool: ...


class _CapabilitySink(Protocol):
    """Where decode evidence goes (PlaybackCapability satisfies it). observe()
    reports whether the verdict changed, so a playback that only confirms what
    was already known writes nothing."""

    def observe(self, report: DecodeReport, played_s: float) -> bool: ...


class PlayerController(QObject):
    errorOccurred = Signal(str)
    subtitleTracksChanged = Signal()
    mediaFormatChanged = Signal()
    stateChanged = Signal()
    mediaContextChanged = Signal()
    # The file's intro / recap / end-credits positions are known (or known to
    # be absent). See application/segments.py.
    segmentsChanged = Signal()
    resumed = Signal(float)
    progressRecorded = Signal()
    # The loaded video is Dolby Vision profile 5: it will render with wrong
    # colours and there is nothing the player can do about it (see
    # _check_dolby_vision). Carries the sentence shown to the viewer.
    playbackWarning = Signal(str)
    # One playback lifecycle event: action ("start" | "pause" | "stop"), the
    # media context, position and duration at that moment. main.py wires it
    # to the Trakt scrobbler; with nothing connected it is inert.
    scrobbleEvent = Signal(str, "QVariantMap", float, float)  # type: ignore[arg-type]
    # Private: mpv reports end-of-stream on its own thread, and everything the
    # recovery does (timers, reloads) has to happen on the GUI thread. A queued
    # signal is the hop.
    _streamEnded = Signal()
    # Same hop, same reason: the track list arrives on mpv's thread, and
    # preparing fast track switching means setting a property on mpv -- which
    # must not happen on the thread mpv delivers its events on.
    _tracksReady = Signal()
    # And again for a URL that never opened. It arrives as an mpv event on
    # mpv's thread, and what it starts is a whole new playback.
    _loadFailed = Signal()
    # mpv has the current file open (hops from mpv's thread, like the above).
    _opened = Signal()

    # Frequent enough that a hard kill costs seconds, not minutes; rare enough
    # that a two-hour film writes ~1400 rows' worth of UPSERTs, not 7 million.
    SAVE_INTERVAL_MS = 5000

    # A dropped connection arrives as end-of-file (see MpvPlayer's stream-ended
    # callback), so recovery is: reload the same URL at the position playback
    # froze at, and keep trying. The delays back off because the usual cause —
    # a router coming back up, a phone switching networks — is not fixed by
    # asking again immediately, and then hold at 30s: someone who walks away
    # from a stalled episode wants it playing when they return, not an error.
    RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 15000, 30000)
    MAX_RECONNECT_ATTEMPTS = 20
    # Attempts for a stream that never opened AT ALL, which is a different
    # failure wearing the same clothes. A dropped connection deserves twenty
    # tries over ten minutes because the network usually comes back; a host
    # that refuses the connection outright does not, and the viewer is sitting
    # in front of a list of other sources that probably work. Retrying that
    # for ten minutes is a spinner, not a recovery.
    MAX_OPEN_ATTEMPTS = 3
    UNREACHABLE_ERROR = "This source's host didn't respond. Try another one from the list."
    SWITCHING_SOURCE = "That source didn't respond. Trying the next one."
    # How many alternatives to carry. Enough to get past a bad CDN node
    # without turning a dead title into a minute of the app trying every
    # source in the list on the viewer's behalf.
    SOURCE_QUEUE_LIMIT = 5
    # A reload that never produces a frame (the stream is gone, not just the
    # network) reports nothing at all, so progress is checked on a deadline.
    RECONNECT_VERIFY_MS = 20000
    # How long a load may take to open before it is treated as a source that
    # never will (see _open_watchdog). Generous for a slow-but-live host: the
    # addon redirect and a CDN connect measured ~2s together on a real link.
    OPEN_TIMEOUT_MS = 15000
    # Within this of the duration, an end-of-file is the file actually ending.
    END_OF_STREAM_SLACK = 5.0
    # A film or episode shorter than this is not the title: it is a host's
    # error clip standing in for it. ElfHosted answers a link signed for an
    # address this machine no longer has with a "Wrong IP" slate, served as
    # a playable video (measured: it played with no duration and ended at
    # 0:01, and stall recovery then reloaded the same link forever). Such a
    # clip is a source that failed, not one that dropped: the next source is
    # tried, and nothing is recorded -- its position would overwrite the
    # viewer's resume point. Trailers carry no media identity and are exempt.
    SLATE_MAX_S = 60.0
    SLATE_ERROR = (
        "This source sent an error clip instead of the video. Try another one from the list."
    )
    # How far past the stall point a reload may land and still count as the
    # stream coming back. A reload resumes where playback stalled; one that
    # lands minutes later did not read the file properly (its index could not
    # be fetched) and is not a recovery -- measured, one landed at the last
    # second of a 42-minute episode and was saved as watched.
    RECOVERY_JUMP_S = 60.0
    # How close to the end of an episode the next one's sources are fetched.
    # Late enough that the viewer has probably committed to finishing it, and
    # early enough that an aggregator's multi-second answer is in (and still
    # inside ResolveStream.REUSE_S) by the time they are back on the list.
    NEARING_END_S = 240.0
    # The only DV profile libmpv's render API cannot render correctly.
    UNSUPPORTED_DV_PROFILE = 5
    DV_WARNING = (
        "This source is Dolby Vision profile 5: its colours will look wrong here. "
        "Try another source."
    )

    def __init__(
        self,
        player_factory: Callable[[], MediaPlayer],
        style_provider: Callable[[], SubtitleStyle] | None = None,
        progress: WatchProgressRepository | None = None,
        speed_sink: _SpeedSink | None = None,
        speed_persist: Callable[[], None] | None = None,
        incompatible_sink: _IncompatibleSink | None = None,
        capability_sink: _CapabilitySink | None = None,
        accelerator: StreamAccelerator | None = None,
        on_nearing_end: Callable[[str, str], None] | None = None,
        links: LinkWarmup | None = None,
        languages_provider: Callable[[], TrackLanguages] | None = None,
        idle_inhibitor: IdleInhibitor | None = None,
        segment_source: SegmentSource | None = None,
        segment_lookup_enabled: Callable[[], bool] | None = None,
        subtitle_finder: Callable[[MediaType, str], Awaitable[list[Subtitle]]] | None = None,
    ) -> None:
        super().__init__()
        # Told (media id, video id) once per playback of a series episode, when
        # it is NEARING_END_S from the end: the Detail controller starts the
        # next episode's sources then.
        self._on_nearing_end = on_nearing_end
        # Links resolved ahead of the click (application/link_warmup.py).
        self._links = links
        self._nearing_end_sent = False
        self._factory = player_factory
        self._style_provider = style_provider
        # Read at every new file, so a change in Settings needs no signal:
        # the next episode simply opens with it.
        self._languages_provider = languages_provider
        # Screen stays on while a video is actually playing. stateChanged is
        # the one place every change of that passes through -- play, stop,
        # pause, and mpv's own pause at the end of a file -- and when it is
        # emitted from mpv's thread this slot still runs on the GUI thread.
        self._idle_inhibitor = idle_inhibitor
        self._inhibiting = False
        if idle_inhibitor is not None:
            self.stateChanged.connect(self._sync_idle_inhibit)
        self._progress = progress
        # Playback is the only time this app reads enough bytes to say anything
        # about the connection, so the autosave tick doubles as the sampler.
        self._speed_sink = speed_sink
        self._speed_persist = speed_persist
        self._speed_sampled = False
        # One warning per playback, not one per track-list update: mpv
        # republishes the list on every track change.
        self._dv_warned = False
        self._incompatible_sink = incompatible_sink
        # What this machine is making of the current file, and for how long.
        # The report is re-read on every tick rather than kept: mpv's drop
        # counters only ever grow within a file, so the last one read is the
        # total, and a playback that ends between ticks still has the one from
        # the tick before it.
        self._capability_sink = capability_sink
        self._decode_report = DecodeReport()
        self._decode_ticks = 0
        # Something worth writing out was learned this playback, beyond the
        # speed samples. Shares _persist_speed's single write (see there).
        self._learned = False
        # Pulls a stream over several connections and serves it on localhost
        # (see infrastructure/network/segmented_proxy.py). Optional, and
        # inert when it cannot help: local_url() hands the URL straight back.
        self._accelerator = accelerator
        # The label of the source now playing. Set by the source list at click
        # time: mpv knows the profile, but only the list knows which row the
        # URL came from, and a debrid URL cannot identify it later.
        self._source_name = ""
        self._source_title = ""
        # The release's file name (setSourceFile), for matching subtitles to it.
        self._source_file = ""
        # What the playing file is, for the badges (see mediaBadges).
        self._media_format = MediaFormat()
        # The sources listed below the one that was clicked. A host that
        # refuses the connection is common with debrid CDNs and says nothing
        # about the title, so the answer is the next source rather than an
        # error message asking the viewer to do what the app can do itself.
        self._queue: list[tuple[str, tuple[tuple[str, str], ...], str, str, str]] = []
        self._player: MediaPlayer | None = None
        self._context: dict[str, str] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(PlayerController.SAVE_INTERVAL_MS)
        self._save_timer.timeout.connect(self._on_tick)
        # --- stall recovery ---
        self._current_url = ""
        self._current_headers: tuple[tuple[str, str], ...] = ()
        # Whether mpv is reading the accelerator's loopback URL rather than
        # the host. Decides where a speed sample may come from.
        self._proxied = False
        self._last_position = 0.0
        self._last_duration = 0.0
        self._reconnecting = False
        self._stall_position = 0.0
        self._reconnect_attempt = 0
        self._user_paused = False
        # Playback held down for the duration of a window resize. Kept apart
        # from _user_paused on purpose: that one records what the VIEWER wants,
        # and the stall recovery consults it to decide whether to start playing
        # again by itself. A resize is not an opinion about playback.
        self._resize_paused = False
        # A resize is in progress (the page reports each geometry change), and
        # within one, the viewer has pressed play or pause themselves. That
        # press has to stick for the rest of the drag: without the second flag
        # the very next geometry change would pause them again, and holding
        # Space against a window drag is not a fight anyone can win.
        self._resize_active = False
        self._resize_override = False
        self._stalled_while_paused = False
        # This URL has produced no frame yet, so a failure to open is a dead
        # source rather than a blip. Reset per playback in play().
        self._never_opened = False
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._reconnect_now)
        self._verify_timer = QTimer(self)
        self._verify_timer.setSingleShot(True)
        self._verify_timer.setInterval(PlayerController.RECONNECT_VERIFY_MS)
        self._verify_timer.timeout.connect(self._verify_reconnect)
        self._streamEnded.connect(self._on_stream_ended)
        # Both end in the same place: a source that stops feeding frames and
        # one that never fed any are answered by the next source, and the
        # handler already tells them apart by what was decoded.
        self._loadFailed.connect(self._on_stream_ended)
        # A host that accepts the connection and then sends nothing fails
        # nothing: no end of file, no load error -- ffmpeg just retries its
        # timed-out connect, and did so for 65s against a silent local server
        # even with mpv's timeout cut to 10s. Every load therefore has until
        # OPEN_TIMEOUT_MS to open, and one that has not is handled exactly as
        # a source that never opened: the next source, or a bounded retry.
        self._open_watchdog = QTimer(self)
        self._open_watchdog.setSingleShot(True)
        self._open_watchdog.setInterval(PlayerController.OPEN_TIMEOUT_MS)
        self._open_watchdog.timeout.connect(self._on_open_timeout)
        self._opened.connect(self._open_watchdog.stop)
        self._opened.connect(self._note_source_opened)
        # Whether the player has confirmed opening this source since play()
        # chose it. Reset per source, not per reconnect: a drop mid-episode is
        # still a source that opened.
        self._source_opened = False
        self._tracksReady.connect(self._prepare_track_switching)
        # Read off the file's chapters once it is open: the track list and the
        # chapter list arrive together, and this runs on the GUI thread.
        self._segments = Segments()
        self._segments_read = False
        # What the segments were read from, kept for the timeline's sections.
        self._chapters: list[tuple[float, str]] = []
        self._segments_duration = 0.0
        self._tracksReady.connect(self._read_segments)
        # The fallback for files whose chapters leave something out (see
        # _look_up_segments), and the Settings toggle that allows it.
        self._segment_source = segment_source
        self._segment_lookup_enabled = segment_lookup_enabled
        # Bumped per file, so an answer that arrives after the next episode
        # started is dropped rather than painted over it.
        self._segments_token = 0
        # Background work (SkipDB lookups, addon subtitles), held until done:
        # a task nothing references can be collected mid-flight.
        self._background: set[asyncio.Task[None]] = set()
        # ---- subtitles ----
        # Files the installed addons offer (the protocol's `subtitles`), asked
        # once per file. Loaded into mpv on a pick -- or by themselves when a
        # language is preferred and the file has no track in it -- and from
        # then on switching to and from them is free: mpv reads an external
        # subtitle whole, where an embedded one costs a refresh seek.
        self._subtitle_finder = subtitle_finder
        self._subtitles_token = 0
        self._online_subtitles: list[Subtitle] = []
        # Loaded into mpv for this file, in order; the one shown last, if it
        # was one of these. Both survive a reconnect, which drops them from
        # mpv, so they can be put back.
        self._added_subtitles: list[Subtitle] = []
        self._shown_online: Subtitle | None = None
        # The addon file the viewer picked, while it downloads (seconds, on a
        # slow host): the menu shows it chosen from the click on, not from
        # when the player has it. And every pick the viewer makes, counted,
        # with the last track they chose -- a file that arrives after they
        # chose something else must not take the screen from it.
        self._picking: Subtitle | None = None
        self._subtitle_picks = 0
        self._subtitle_choice: int | None = None
        self._subtitles_settled = False
        self._file_has_subtitle = False
        self._online_auto_done = False
        self._readd_subtitles = False
        self._tracksReady.connect(self._settle_subtitles)
        self._tracksReady.connect(self._refresh_media_format)
        self._opened.connect(self._refresh_media_format)

    def _ensure(self) -> MediaPlayer | None:
        if self._player is None:
            try:
                self._player = self._factory()
            except PlaybackFailed as exc:
                self.errorOccurred.emit(str(exc))
                return None
            self._player.set_tracks_changed_callback(self._on_tracks_changed)
            self._player.set_state_changed_callback(self._on_state_changed)
            self._player.set_stream_ended_callback(self._streamEnded.emit)
            self._player.set_load_failed_callback(self._loadFailed.emit)
            self._player.set_opened_callback(self._opened.emit)
            self.applySubtitleStyle()
        return self._player

    def _on_tracks_changed(self) -> None:
        # May be invoked from mpv's own thread; Signal.emit() is safe to call
        # from any thread and is delivered to QML via a queued connection.
        self.subtitleTracksChanged.emit()
        self._tracksReady.emit()
        self._check_dolby_vision()

    def _prepare_track_switching(self) -> None:
        """Let the player make later track changes cheap, now that it knows
        what the tracks are. Runs here rather than in the player's own
        track-list observer because that one fires on mpv's event thread, and
        what this does is set a property."""
        if self._player is None:
            return
        self._player.prepare_track_switching()

    def _check_dolby_vision(self) -> None:
        """Say so when the file is one the renderer cannot get right.

        Dolby Vision profile 5 carries IPT-C2 colour, which only libplacebo
        converts; libmpv's render API (the only way to draw video inside a Qt
        scene) runs the older vo_gpu renderer, so profile 5 plays magenta on
        every platform and every backend here. The viewer is told once, and
        told what to do about it -- the addon's "DV" label cannot distinguish
        this from profile 8, which is fine, so the player is the only honest
        source for the warning.
        """
        if self._dv_warned or self._player is None:
            return
        if self._player.video_dolby_vision_profile() != PlayerController.UNSUPPORTED_DV_PROFILE:
            return
        self._dv_warned = True
        _log.info("source is Dolby Vision profile 5; colours will be wrong (no DV in vo_gpu)")
        self.playbackWarning.emit(PlayerController.DV_WARNING)
        # Evidence, not a guess: remember this exact release so the source
        # list can keep it out of the way next time.
        if self._incompatible_sink is not None and self._source_name:
            learned = self._incompatible_sink.remember(self._source_name, self._source_title)
            if learned and self._speed_persist is not None:
                self._speed_persist()

    def _on_state_changed(self) -> None:
        self.stateChanged.emit()

    def _read_segments(self) -> None:
        """Classify the file's chapters, once per file. Waits for a duration:
        "is this chapter in the second half" needs one, and the autosave tick
        retries until there is."""
        if self._segments_read or self._player is None:
            return
        duration = self._player.duration()
        if duration <= 0:
            return
        self._segments_read = True
        self._chapters = self._player.chapters()
        self._segments_duration = duration
        self._segments = classify(self._chapters, duration)
        if self._segments.intro is None or self._segments.credits_start is None:
            self._look_up_segments(duration)
        if self._segments != Segments():
            _log.info(
                "chapters mark intro %s, recap %s, credits at %s",
                self._segments.intro,
                self._segments.recap,
                self._segments.credits_start,
            )
        self.segmentsChanged.emit()

    def _look_up_segments(self, duration: float) -> None:
        """Ask the SegmentSource for what the file's chapters left out.

        A fallback only: nothing is asked when the file names its intro and
        its credits, and whatever it does name is kept over the answer. Only
        for an episode with an IMDb id and a Cinemeta-style video id
        ("tt123:1:7"), which is what the source is keyed by.
        """
        source = self._segment_source
        if source is None or (
            self._segment_lookup_enabled is not None and not self._segment_lookup_enabled()
        ):
            return
        parts = self._context.get("videoId", "").split(":")
        if len(parts) != 3 or not parts[0].startswith("tt"):
            return
        imdb_id, season_text, episode_text = parts
        if not (season_text.isdigit() and episode_text.isdigit()):
            return
        season, episode = int(season_text), int(episode_text)
        token = self._segments_token

        async def look_up() -> None:
            found = await source.segments(imdb_id, season, episode, duration)
            if found is None or token != self._segments_token:
                return
            own = self._segments
            merged = Segments(
                intro=own.intro or found.intro,
                recap=own.recap or found.recap,
                credits_start=own.credits_start
                if own.credits_start is not None
                else found.credits_start,
            )
            if merged == own:
                return
            _log.info(
                "SkipDB fills in intro %s, recap %s, credits at %s",
                merged.intro,
                merged.recap,
                merged.credits_start,
            )
            self._segments = merged
            self.segmentsChanged.emit()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop running (a synchronous test): nothing to ask
        task = loop.create_task(look_up())
        # Held until done: a task nothing references can be collected mid-flight.
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # Seconds into the file, -1 where the chapters say nothing: the player's
    # skip button and the next-episode card read these.
    @Property(float, notify=segmentsChanged)
    def introStart(self) -> float:
        return self._segments.intro[0] if self._segments.intro else -1.0

    @Property(float, notify=segmentsChanged)
    def introEnd(self) -> float:
        return self._segments.intro[1] if self._segments.intro else -1.0

    @Property(float, notify=segmentsChanged)
    def recapStart(self) -> float:
        return self._segments.recap[0] if self._segments.recap else -1.0

    @Property(float, notify=segmentsChanged)
    def recapEnd(self) -> float:
        return self._segments.recap[1] if self._segments.recap else -1.0

    @Property("QVariantList", notify=segmentsChanged)  # type: ignore[arg-type]
    def timelineSections(self) -> list[dict[str, object]]:
        """{start, end, title, kind} for the timeline to split at: every
        chapter, and the edges of the intro, recap and credits however they
        were learnt. Empty when there is nothing to split."""
        return [
            {"start": s.start, "end": s.end, "title": s.title, "kind": s.kind}
            for s in sections(self._chapters, self._segments, self._segments_duration)
        ]

    @Property(float, notify=segmentsChanged)
    def creditsStart(self) -> float:
        start = self._segments.credits_start
        return start if start is not None else -1.0

    def _sync_idle_inhibit(self) -> None:
        """Hold the screen awake exactly while a file is loaded and not paused.
        Buffering counts as playing (mpv's `pause` stays false through a
        stall): the viewer is still watching, and dimming mid-spinner would
        be the wrong answer to a slow host."""
        playing = (
            self._player is not None and bool(self._current_url) and not self._player.is_paused()
        )
        if playing == self._inhibiting or self._idle_inhibitor is None:
            return
        self._inhibiting = playing
        self._idle_inhibitor.set_inhibited(playing)

    # --- stall recovery ---
    #
    # A stream that dies mid-episode does not report an error. ffmpeg turns the
    # failed read into end-of-file, mpv finishes the file and holds the last
    # frame paused, and playback simply stops — no spinner, and no retry when
    # the connection returns. mpv's own reconnect options (see the player
    # adapter) ride out a blip; this handles the case where the network was
    # gone long enough that even those gave up.

    def _reset_recovery(self) -> None:
        self._open_watchdog.stop()
        self._reconnect_timer.stop()
        self._verify_timer.stop()
        self._current_url = ""
        self._current_headers = ()
        self._reconnecting = False
        self._stalled_while_paused = False
        self._reconnect_attempt = 0
        self._stall_position = 0.0
        self._last_position = 0.0
        self._last_duration = 0.0
        self._user_paused = False

    def _note_source_opened(self) -> None:
        self._source_opened = True

    def _served_slate(self, duration: float) -> bool:
        """A film or episode's source that opened and turned out to be a short
        clip, or one with no duration at all (see SLATE_MAX_S)."""
        if not self._context.get("mediaId") or not self._source_opened:
            return False
        return duration < self.SLATE_MAX_S

    def _landed_astray(self, position: float) -> bool:
        """A reload that came up nowhere near where the stream stalled."""
        return self._reconnecting and position - self._stall_position > self.RECOVERY_JUMP_S

    def _note_progress(self, position: float) -> bool:
        """Remember where playback is, and notice a reload that took. False
        when the position is not one to believe (see _landed_astray)."""
        if self._landed_astray(position):
            # Not recorded and not a recovery: the verify timer, or the end of
            # file this usually runs into, schedules the next attempt at the
            # stall point.
            return False
        if position > 0:
            self._last_position = position
        if self._reconnecting and position > self._stall_position + 0.25:
            _log.info("stream recovered at %.0fs", position)
            self._reconnect_timer.stop()
            self._verify_timer.stop()
            self._reconnecting = False
            self._reconnect_attempt = 0
            self.stateChanged.emit()
        return True

    def _on_stream_ended(self) -> None:
        """mpv stopped feeding frames, or never started.

        Three cases arrive here, and the difference between them is what was
        decoded before it happened: the episode ended, the connection dropped
        mid-episode, or the URL never opened at all (a refused CDN node, which
        mpv reports as an END_FILE error and no end of file).
        """
        self._open_watchdog.stop()
        if self._player is None or not self._current_url:
            return
        if self._reconnect_timer.isActive():
            return  # a retry is already armed; this is its failure arriving
        position = self._player.position() or self._last_position
        duration = self._player.duration() or self._last_duration
        if self._served_slate(duration):
            _log.warning(
                "the source played a %.0fs clip instead of the title (a host's error slate)",
                duration,
            )
            self._reconnecting = False
            self._never_opened = False
            self.stateChanged.emit()
            if not self._user_paused and self._try_next_source():
                return
            self._current_url = ""
            self.errorOccurred.emit(self.SLATE_ERROR)
            return
        if (
            duration > 0
            and position >= duration - self.END_OF_STREAM_SLACK
            and not self._landed_astray(position)
        ):
            return  # the file really ended
        # Nothing was ever decoded from this URL: no duration, and either no
        # position or none the player ever opened the file to reach. The
        # position alone lied for a resumed episode: `start` put 218s there
        # before a byte was read, and a source that never opened was retried
        # as a dropped connection instead of handing over to the next one.
        # Never opened and dropped want opposite amounts of patience.
        self._never_opened = duration <= 0 and (position <= 0 or not self._source_opened)
        if self._never_opened and not self._user_paused and self._try_next_source():
            return
        if not self._reconnecting:
            self._stall_position = position
            self._reconnect_attempt = 0
            if self._user_paused:
                # Reloading now would start playing under someone who chose to
                # stop. Wait for them to press play.
                self._stalled_while_paused = True
                return
            _log.warning(
                "stream ended at %.0fs of %.0fs; treating as a dropped connection",
                position,
                duration,
            )
            self._reconnecting = True
            self.stateChanged.emit()
        self._schedule_reconnect()

    def _resume_after_stall(self) -> None:
        """Play pressed on a stream that died while paused: reload it now."""
        if not self._stalled_while_paused or self._reconnecting:
            return
        self._stalled_while_paused = False
        self._reconnecting = True
        self._reconnect_attempt = 0
        self.stateChanged.emit()
        self._reconnect_now()

    def _schedule_reconnect(self) -> None:
        limit = self.MAX_OPEN_ATTEMPTS if self._never_opened else self.MAX_RECONNECT_ATTEMPTS
        if self._reconnect_attempt >= limit:
            _log.error("giving up on the stream after %d attempts", self._reconnect_attempt)
            self._reconnecting = False
            self._never_opened = False
            self.stateChanged.emit()
            self.errorOccurred.emit(
                self._unreachable_message()
                if limit == self.MAX_OPEN_ATTEMPTS
                else "Lost the connection to this stream."
            )
            return
        self._verify_timer.stop()
        index = min(self._reconnect_attempt, len(self.RECONNECT_DELAYS_MS) - 1)
        self._reconnect_attempt += 1
        self._reconnect_timer.start(self.RECONNECT_DELAYS_MS[index])

    def _reconnect_now(self) -> None:
        if self._player is None or not self._current_url or not self._reconnecting:
            return
        _log.info(
            "reconnecting to the stream at %.0fs (attempt %d)",
            self._stall_position,
            self._reconnect_attempt,
        )
        try:
            # Re-registered, not reused: the accelerator tore its fetch down
            # when mpv dropped the connection, so the previous local URL names
            # a stream nothing is filling any more.
            play_url = self._accelerated(self._current_url)
            self._proxied = play_url != self._current_url
            self._player.play(
                play_url,
                start=self._stall_position,
                headers=self._current_headers,
                upstream_cached=self._upstream_cached(self._current_url, play_url),
            )
        except PlaybackFailed as exc:
            _log.warning("reconnect attempt failed: %s", exc)
            self._schedule_reconnect()
            return
        self._user_paused = False
        # The reload dropped every subtitle file added to the old load.
        self._readd_subtitles = bool(self._added_subtitles)
        # A reload that never opens the file reports nothing back, so the only
        # proof it worked is the playhead moving on before this fires.
        self._verify_timer.start()
        self._open_watchdog.start()

    def _on_open_timeout(self) -> None:
        if self._player is None or not self._current_url:
            return
        _log.warning(
            "source did not open within %ds; treating it as one that never will",
            PlayerController.OPEN_TIMEOUT_MS // 1000,
        )
        self._on_stream_ended()

    def _verify_reconnect(self) -> None:
        if self._reconnecting and not self._reconnect_timer.isActive():
            self._schedule_reconnect()

    # --- playback state (notify: stateChanged) ---

    @Property(bool, notify=stateChanged)
    def paused(self) -> bool:
        return self._player.is_paused() if self._player is not None else False

    @Property(float, notify=stateChanged)
    def duration(self) -> float:
        if self._player is None:
            return 0.0
        duration = self._player.duration()
        if duration > 0:
            # Remembered because a stalled stream reports 0 once mpv unloads
            # it, and "was this the end of the file?" needs the real length.
            self._last_duration = duration
        return duration

    @Property(bool, notify=stateChanged)
    def reconnecting(self) -> bool:
        """True while the stream is being re-opened after a dropped connection
        — the player page labels its spinner with it."""
        return self._reconnecting

    @Property(float, notify=stateChanged)
    def volume(self) -> float:
        return self._player.volume() if self._player is not None else 100.0

    @Property(bool, notify=stateChanged)
    def muted(self) -> bool:
        return self._player.is_muted() if self._player is not None else False

    @Property(float, notify=stateChanged)
    def videoAspect(self) -> float:
        """Width/height of the decoded frame, for the PiP tile to size itself
        by. 16:9 whenever that is not (yet) knowable — before the first frame
        mpv reports nothing, and a tile has to have some shape immediately."""
        if self._player is None:
            return 16 / 9
        width, height = self._player.video_size()
        if width > 0 and height > 0:
            return width / height
        return 16 / 9

    @Property(str, notify=mediaContextChanged)
    def mediaTitle(self) -> str:
        """What is playing, for the player's own chrome. Empty for playback
        with no identity (trailers), which hides the overlay."""
        return self._context.get("name", "")

    @Property(str, notify=mediaContextChanged)
    def videoId(self) -> str:
        """The episode playing, or "" for a movie. The episode panel marks
        this row as the one on screen."""
        return self._context.get("videoId", "")

    @Property(str, notify=mediaContextChanged)
    def mediaLabel(self) -> str:
        """The episode line under the title ("S1E3 · Red Tide"); empty for
        movies."""
        return self._context.get("label", "")

    @Slot(result=float)
    def position(self) -> float:
        """Polled by a QML Timer while the player page is open — cheaper than
        observing time-pos, which fires many times a second."""
        if self._player is None:
            return 0.0
        position = self._player.position()
        self._note_progress(position)
        return position

    @Slot(result=bool)
    def isLoading(self) -> bool:
        """Polled with position: buffering, mid-seek, or waiting on a reload
        after the connection dropped."""
        if self._reconnecting:
            return True
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
        self._reset_recovery()
        self._current_url = url
        self._current_headers = tuple(
            (str(key), str(value)) for key, value in (headers or {}).items()
        )
        # A link resolved while the list was being read skips the addon's own
        # redirect (a debrid lookup, ~1.3s measured). Only the first load uses
        # it: _current_url stays the addon's URL, so a reconnect an hour in
        # asks for a fresh link rather than replaying one that may have
        # expired. If the resolved one fails to open, the addon's URL is the
        # first fallback -- and `take` has already dropped the resolved link,
        # so that retry really does go through the addon.
        resolved = self._links.take(url) if self._links is not None else None
        if resolved is not None:
            _log.info("starting on the link resolved ahead of the click")
            self._queue.insert(
                0,
                (
                    url,
                    self._current_headers,
                    self._source_name,
                    self._source_title,
                    self._source_file,
                ),
            )
        # What mpv is actually told to open. Kept apart from _current_url so
        # stall recovery re-registers with the accelerator instead of
        # reloading a token whose fetch has already been torn down.
        play_url = self._accelerated(resolved or url)
        self._proxied = play_url != (resolved or url)
        self._last_position = start
        # The last file's badges must not stand over this one's first seconds.
        self._refresh_media_format(MediaFormat())
        self._dv_warned = False
        self._never_opened = False
        self._source_opened = False
        # A new file; a reconnect reloads the same one and keeps them.
        self._segments = Segments()
        self._segments_read = False
        self._chapters = []
        self._segments_duration = 0.0
        self._segments_token += 1
        self.segmentsChanged.emit()
        self._reset_subtitles()
        self._nearing_end_sent = False
        # A new file resets mpv's own drop counters, so the evidence has to
        # restart with it -- carrying the previous file's count over would
        # convict this one of its predecessor's stutter.
        self._decode_report = DecodeReport()
        self._decode_ticks = 0
        title = self._context.get("name") or "?"
        label = self._context.get("label") or ""
        _log.info(
            "playing %s%s%s",
            title,
            f": {label}" if label else "",
            f" (resuming at {start:.0f}s)" if start > 0 else "",
        )
        if self._languages_provider is not None:
            # Here and not in _reconnect_now: a reload of the same file must
            # keep the track the viewer switched to mid-episode.
            player.set_track_languages(self._languages_provider())
        try:
            # behaviorHints.proxyHeaders.request from the chosen stream: some
            # addons 403 without their Referer/User-Agent.
            player.play(
                play_url,
                start=start,
                headers=self._current_headers,
                upstream_cached=self._upstream_cached(resolved or url, play_url),
            )
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))
            return
        self._open_watchdog.start()
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
            open_in_browser(url)

    @Slot()
    def stop(self) -> None:
        self._note_manual_playback_change()
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
            # Before player.stop(): stopping ends the stream, and an unarmed
            # recovery would read that as a drop and reload the episode.
            self._reset_recovery()
            self._queue = []
            self._player.stop()
            self.stateChanged.emit()
            self._persist_decode()
            self._persist_speed()

    def _persist_speed(self) -> None:
        """Write what this playback learned -- speed samples and any decode
        verdict -- out once, at the end of it, not on every 5s tick, which
        would turn a film into a few hundred rewrites of the settings file for
        values nothing reads until the next Sources page."""
        if self._speed_persist is None or not (self._speed_sampled or self._learned):
            return
        self._speed_sampled = False
        self._learned = False
        self._speed_persist()

    @Slot()
    def pause(self) -> None:
        if self._player is not None:
            self._note_manual_playback_change()
            _log.debug("paused at %.0fs", self._player.position())
            self._user_paused = True
            self._player.pause()
            self._record()
            self._emit_scrobble("pause")

    @Slot()
    def resume(self) -> None:
        if self._player is not None:
            self._note_manual_playback_change()
            _log.debug("resumed at %.0fs", self._player.position())
            self._user_paused = False
            self._player.resume()
            self._emit_scrobble("start")
            self._resume_after_stall()

    @Slot()
    def togglePause(self) -> None:
        if self._player is None:
            return
        # A deliberate press during a resize wins outright: neither the settle
        # nor the rest of the drag may undo it.
        self._note_manual_playback_change()
        if self._player.is_paused():
            self._user_paused = False
            self._player.resume()
            self._emit_scrobble("start")
            self._resume_after_stall()
        else:
            self._user_paused = True
            self._player.pause()
            self._emit_scrobble("pause")
        self.stateChanged.emit()

    def _note_manual_playback_change(self) -> None:
        """The viewer just decided something about playback themselves."""
        self._resize_paused = False
        if self._resize_active:
            self._resize_override = True

    @Slot()
    def suspendForResize(self) -> None:
        """Hold playback still while the window is being dragged to a new size.

        Rendering video into a window the compositor is resizing is expensive
        on the thread that has to hand it its new contents: measured on an M2,
        frames arrive with gaps up to 264 ms during a resize with video playing,
        and up to 27 ms with it paused. Pausing also means the film does not run
        on unwatched while the viewer is busy sizing the tile.

        Nothing here is a playback decision, so no scrobble is emitted (Trakt
        would otherwise collect a pause and a start for every drag) and
        _user_paused is left alone. A video the viewer paused themselves is left
        exactly as it is, and is not resumed afterwards.
        """
        self._resize_active = True
        if self._player is None or self._resize_paused or self._resize_override:
            return
        if self._player.is_paused():
            return
        self._resize_paused = True
        self._player.pause()
        self.stateChanged.emit()

    @Slot()
    def resumeAfterResize(self) -> None:
        """Play on once the drag is over -- only if suspendForResize is what
        stopped it."""
        self._resize_active = False
        self._resize_override = False
        if not self._resize_paused:
            return
        self._resize_paused = False
        if self._player is not None:
            self._player.resume()
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

    @Slot(str, str)
    def setSourceLabel(self, name: str, title: str) -> None:
        """Which row in the source list this playback came from. Called before
        play(); a stream URL is single-use, so the label is the only thing that
        still identifies the release once mpv reports on it."""
        self._source_name = name
        self._source_title = title

    @Slot(str)
    def setSourceFile(self, filename: str) -> None:
        """The release's file name, beside setSourceLabel. An aggregator's
        label can leave out the episode's title, and one addon's "S06E01" was
        two different episodes (Jared Has Aides, Freak Strike) depending on the
        release: the file name is what says which one is playing, and so which
        subtitle files belong to it."""
        self._source_file = filename

    @Slot("QVariantList")
    def setSourceQueue(self, entries: list[dict[str, object]]) -> None:
        """The sources to fall back to, best first. Set at click time beside
        setSourceLabel; an empty list means the viewer picked the last one."""
        queue: list[tuple[str, tuple[tuple[str, str], ...], str, str, str]] = []
        # Capped after the filter, not before: an entry with nothing playable
        # is not a fallback, and slicing first let one at the top of the list
        # cost a real one at the bottom.
        for entry in entries:
            if len(queue) >= PlayerController.SOURCE_QUEUE_LIMIT:
                break
            url = str(entry.get("url") or "")
            if not url:
                continue
            raw = entry.get("headers") or {}
            headers = tuple(
                (str(key), str(value))
                for key, value in dict(raw).items()  # type: ignore[call-overload]
            )
            queue.append(
                (
                    url,
                    headers,
                    str(entry.get("name") or ""),
                    str(entry.get("title") or ""),
                    str(entry.get("filename") or ""),
                )
            )
        self._queue = queue

    def _try_next_source(self) -> bool:
        """Move to the next source, if there is one. Returns whether it did.

        Deliberately immediate rather than after the retries: by the time this
        runs ffmpeg has already retried the URL several times on its own, so
        the source has had its chances and every further second is a spinner.
        """
        if not self._queue:
            return False
        url, headers, name, title, filename = self._queue.pop(0)
        _log.warning(
            "source did not open; switching to the next one (%d left after this)",
            len(self._queue),
        )
        self._reconnecting = False
        self._never_opened = False
        self.stateChanged.emit()
        self.playbackWarning.emit(PlayerController.SWITCHING_SOURCE)
        self.setSourceLabel(name, title)
        self.setSourceFile(filename)
        self.play(url, dict(headers))
        return True

    @Slot()
    def flushProgress(self) -> None:
        """Record now. Wired to aboutToQuit so the last seconds survive."""
        self._record()
        # Quitting mid-episode is the common case, and it never reaches stop().
        self._persist_decode()
        self._persist_speed()

    def is_recording(self) -> bool:
        """True while the autosave timer is live. Not a Slot — QML has no use
        for it; it exists so a test can assert the timer's lifecycle without
        waiting out a real interval."""
        return self._save_timer.isActive()

    def _on_tick(self) -> None:
        if self._player is not None and not self._player.is_paused():
            if not self._note_progress(self._player.position()):
                return
            self._read_segments()
            self._record()
            self._sample_speed()
            self._sample_decode()
            self._check_nearing_end()
            # HDR10+ (on frames) and mpv's transfer (on the decoded picture)
            # are known only once frames are.
            self._refresh_media_format()

    def _check_nearing_end(self) -> None:
        if self._nearing_end_sent or self._on_nearing_end is None or self._player is None:
            return
        if self._context.get("type") != "series" or not self._context.get("videoId"):
            return
        duration = self._player.duration()
        if duration <= 0 or duration - self._player.position() > PlayerController.NEARING_END_S:
            return
        self._nearing_end_sent = True
        self._on_nearing_end(self._context.get("mediaId", ""), self._context["videoId"])

    def _sample_decode(self) -> None:
        """One decode observation per tick of unpaused playback.

        Counting ticks rather than reading the clock is what makes the
        denominator honest: the timer only fires while something is actually
        playing, so a film left paused for an hour does not turn into an hour
        of evidence that this machine drops no frames.
        """
        if self._capability_sink is None or self._player is None:
            return
        self._decode_report = self._player.decode_report()
        self._decode_ticks += 1

    def _persist_decode(self) -> None:
        """File what this playback taught, once, when it ends. Nothing is
        written when the verdict did not move -- the common case, since most
        playbacks confirm what the machine already showed."""
        sink = self._capability_sink
        if sink is None or not self._decode_ticks:
            return
        played_s = self._decode_ticks * (PlayerController.SAVE_INTERVAL_MS / 1000.0)
        report, self._decode_report = self._decode_report, DecodeReport()
        self._decode_ticks = 0
        if not sink.observe(report, played_s):
            return
        _log.info(
            "decode verdict updated: %s at %dp dropped %d frames in %.0fs",
            report.codec or "?",
            report.height,
            report.dropped_frames,
            played_s,
        )
        # Not a write of its own: _persist_speed runs straight after and
        # saves the whole settings payload, this verdict included. Two calls
        # would rewrite the same file twice for one playback.
        self._learned = True

    def _unreachable_message(self) -> str:
        """What to tell the viewer about a source that never opened.

        Names the host whenever something knows it. Every stream URL here is
        an addon signing endpoint that redirects to a CDN node, so the failure
        the player sees names the addon while the host that actually refused
        is only known inside the accelerator -- and "it didn't respond" reads
        like a broken app when what happened is one dead debrid node, or a
        resolver sinkholing the domain.
        """
        reason: str | None = None
        if self._accelerator is not None:
            with contextlib.suppress(Exception):  # pragma: no cover - defensive
                reason = self._accelerator.last_failure()
        if not reason:
            return self.UNREACHABLE_ERROR
        return f"{reason} Try another source from the list."

    def _accelerated(self, url: str) -> str:
        """The URL to hand mpv: the accelerator's local one when it can serve
        this stream, the original otherwise. Never raises -- a proxy that
        cannot help must not be able to stop a playback."""
        if self._accelerator is None:
            return url
        try:
            return self._accelerator.local_url(url, self._current_headers)
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning("stream accelerator declined %s: %s", abbreviate_url(url), exc)
            return url

    def _upstream_cached(self, url: str, play_url: str) -> bool:
        """Whether mpv is about to read `url` through an accelerator that keeps
        the stream on disk itself -- in which case mpv's own cache can be
        small, and a track switch's refresh seek reads local disk rather than
        the host. A URL the accelerator handed straight back is not cached
        by anything but mpv."""
        if self._accelerator is None or play_url == url:
            return False
        try:
            return self._accelerator.holds_stream_cache()
        except Exception:  # pragma: no cover - defensive
            return False

    def _sample_speed(self) -> None:
        """One bandwidth observation, taken from the player's own read speed.

        Deliberately not a measurement of its own: mpv is already pulling the
        stream, so this costs nothing and touches no third party. A paused or
        local playback reads 0 and is discarded by the sink.
        """
        if self._speed_sink is None or self._player is None:
            return
        # With the accelerator in the path, mpv is reading from 127.0.0.1 and
        # its own cache-speed describes a loopback socket, not anyone's
        # internet. The bytes the accelerator pulled from the real host are
        # the only measurement then, and 0.0 from it (nothing fetched lately:
        # its read-ahead is full, or mpv is re-reading its disk cache) is no
        # sample at all. Falling back to mpv there recorded the proxy's disk
        # read speed as the line: 843 Mbps on a 260 Mbps connection, four
        # identical samples in a row, which the high-water estimate then
        # believed for a week.
        if self._proxied and self._accelerator is not None:
            bytes_per_s = self._accelerator.upstream_bytes_per_s()
        else:
            bytes_per_s = self._player.download_speed()
        if bytes_per_s <= 0:
            return
        self._speed_sink.record(int(bytes_per_s * 8 / 1000))
        self._speed_sampled = True

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
        if duration < self.SLATE_MAX_S:
            return  # a host's error clip, not the title (see SLATE_MAX_S)
        media_type: MediaType = "series" if self._context.get("type") == "series" else "movie"
        self._progress.record(
            media_id=media_id,
            video_id=self._context.get("videoId", ""),
            type=media_type,
            name=self._context.get("name", ""),
            poster=self._context.get("poster") or None,
            label=self._context.get("label", ""),
            # Mid-reconnect, where the stream stalled: wherever mpv is sitting
            # then is a reload still settling, or one that landed astray.
            position=self._stall_position if self._reconnecting else self._player.position(),
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

    # Menu ids at and above this are an addon's subtitle files, not yet loaded:
    # their index in _online_menu(). mpv's own track ids never get near it.
    ONLINE_SUBTITLE_ID = 1_000_000
    # How many of the preferred language's files the menu lists; every other
    # language gets its best one.
    ONLINE_PER_PREFERRED = 5
    ONLINE_MENU_LIMIT = 25

    def _reset_subtitles(self) -> None:
        """A new file: nothing loaded, nothing known, and the addons asked."""
        self._subtitles_token += 1
        self._online_subtitles = []
        self._added_subtitles = []
        self._shown_online = None
        self._picking = None
        self._subtitles_settled = False
        self._file_has_subtitle = False
        self._online_auto_done = False
        self._readd_subtitles = False
        finder = self._subtitle_finder
        media_type = self._context.get("type", "")
        video = self._context.get("videoId") or self._context.get("mediaId", "")
        if finder is None or media_type not in ("movie", "series") or not video:
            return
        token = self._subtitles_token
        kind: MediaType = "series" if media_type == "series" else "movie"

        async def fetch() -> None:
            try:
                found = await finder(kind, video)
            except Exception as exc:  # an addon's failure is never playback's
                _log.info("addon subtitles unavailable for %s: %r", video, exc)
                return
            if token != self._subtitles_token:
                return
            self._online_subtitles = found
            if found:
                _log.info("addons offer %d subtitle files for %s", len(found), video)
            self.subtitleTracksChanged.emit()
            self._maybe_load_online()

        self._spawn(fetch())

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Run `coroutine` on the app's loop, held until done. Without a
        running loop (a synchronous test) it is closed unrun."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coroutine.close()
            return
        task = loop.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _preferred_subtitle(self) -> str:
        if self._languages_provider is None:
            return ""
        return self._languages_provider().subtitle

    def _settle_subtitles(self) -> None:
        """On the track list (GUI thread): once per file, let the player fall
        back to an untagged track in the preferred language, then see whether
        an addon's file is needed. After a reconnect, put back the files the
        reload dropped."""
        if self._player is None:
            return
        if self._readd_subtitles:
            self._readd_subtitles = False
            self._spawn(self._add_again(list(self._added_subtitles), self._shown_online))
            return
        if self._subtitles_settled:
            return
        # Not a list to judge: mpv reports an EMPTY track list when the
        # callback is registered and again as each new file starts loading,
        # before the real one arrives already selected. Settling on the empty
        # one read as "the file has no subtitles" and loaded an addon's file
        # over the file's own English track -- out of sync, since an addon's
        # is timed to whatever release its uploader had.
        if not self._player.audio_tracks() and not self._player.subtitle_tracks():
            return
        self._subtitles_settled = True
        self._player.choose_fallback_subtitle()
        # From the track list, not the selection: mpv lists a file's tracks
        # before it chooses among them, and "nothing chosen yet" read as "no
        # English here" loaded an addon's file -- timed to another release --
        # over the file's own English track.
        self._file_has_subtitle = self._player.has_preferred_subtitle()
        self._maybe_load_online()

    def _online_in(self, language: str) -> list[Subtitle]:
        """The addons' files in `language`: its most specific tag first
        ("pt-BR" before "por"), then the ones made for the release that is
        playing. An addon's file is timed to its uploader's release, and one
        naming the same group, source and resolution usually matches it."""
        codes = [code.lower() for code in languages.track_codes(language)]
        matching = [s for s in self._online_subtitles if s.lang.lower() in codes]
        release = _release_words(f"{self._source_name} {self._source_title} {self._source_file}")
        # Words most of the files share (the show's name) say nothing about
        # which release one was made for; left in, "South Park - S06E01" tied
        # with "S6E01 - Freak Strike" for a Freak Strike file.
        words = {id(s): _release_words(s.label) for s in matching}
        counts: dict[str, int] = {}
        for found in words.values():
            for word in found:
                counts[word] = counts.get(word, 0) + 1
        distinctive = {w for w in release if counts.get(w, 0) * 2 <= len(matching)}
        return sorted(
            matching,
            key=lambda s: (codes.index(s.lang.lower()), -len(distinctive & words[id(s)])),
        )

    def _maybe_load_online(self) -> None:
        """A language is preferred, the file has no track in it, and an addon
        does: show that one. Once per file."""
        if self._online_auto_done or not self._subtitles_settled or self._file_has_subtitle:
            return
        preferred = self._preferred_subtitle()
        if not preferred or preferred == languages.SUBTITLES_OFF:
            return
        candidates = self._online_in(preferred)
        if not candidates:
            return
        self._online_auto_done = True
        _log.info("the file has no %s subtitles; loading an addon's", preferred)
        self._spawn(self._load_online(candidates[0], self._subtitle_picks))

    def _online_title(self, subtitle: Subtitle) -> str:
        language = languages.by_tag(subtitle.lang)
        name = language.name if language is not None else (subtitle.lang or "Unknown")
        return f"{name} · {subtitle.label}" if subtitle.label else name

    def _online_menu(self) -> list[Subtitle]:
        """What the subtitle menu offers from the addons: up to
        ONLINE_PER_PREFERRED in the preferred language, then the first of
        every other language (by name), none already loaded."""
        loaded = {s.url for s in self._added_subtitles}
        waiting = [s for s in self._online_subtitles if s.url not in loaded]
        preferred = self._preferred_subtitle()
        mine = self._online_in(preferred)[: self.ONLINE_PER_PREFERRED] if preferred else []
        others: dict[str, Subtitle] = {}
        for subtitle in waiting:
            if subtitle in mine:
                continue
            language = languages.by_tag(subtitle.lang)
            key = language.name if language is not None else subtitle.lang or "?"
            others.setdefault(key, subtitle)
        menu = [s for s in mine if s.url not in loaded] + [others[k] for k in sorted(others)]
        return menu[: self.ONLINE_MENU_LIMIT]

    async def _load_online(self, subtitle: Subtitle, picks: int) -> None:
        """Loads `subtitle`; `picks` is how many picks the viewer had made when
        it was asked for -- counted then, not when this starts to run, which
        is after anything else queued on the loop."""
        player = self._player
        if player is None:
            return
        token = self._subtitles_token
        title = self._online_title(subtitle)
        try:
            await asyncio.to_thread(player.add_subtitle, subtitle.url, title, subtitle.lang)
        except Exception as exc:
            _log.warning("could not load %s subtitles from %s: %r", title, subtitle.addon, exc)
            if token == self._subtitles_token and self._picking is subtitle:
                self._picking = None
                self.subtitleTracksChanged.emit()
            return
        if token != self._subtitles_token:
            return
        if subtitle not in self._added_subtitles:
            self._added_subtitles.append(subtitle)
        _log.info("subtitles: loaded %r from %s", title, subtitle.addon or "an addon")
        # The player shows a file as it adds it. Wanted if this is still the
        # viewer's pick, or if it was loaded for them and they have not
        # chosen anything since.
        if self._picking is subtitle or (self._picking is None and self._subtitle_picks == picks):
            self._shown_online = subtitle
        elif self._picking is None:
            # They chose a track of their own meanwhile: that stays on screen.
            player.set_subtitle_track(self._subtitle_choice)
        if self._picking is subtitle:
            self._picking = None
        self.subtitleTracksChanged.emit()

    async def _add_again(self, files: list[Subtitle], shown: Subtitle | None) -> None:
        player = self._player
        if player is None:
            return
        token = self._subtitles_token
        for subtitle in files:
            if token != self._subtitles_token:
                return
            try:
                await asyncio.to_thread(
                    player.add_subtitle,
                    subtitle.url,
                    self._online_title(subtitle),
                    subtitle.lang,
                    select=subtitle == shown,
                )
            except Exception as exc:
                _log.warning("could not restore subtitles after reconnecting: %r", exc)
        self.subtitleTracksChanged.emit()

    @Slot(float, result=float)
    def shiftSubtitles(self, delta: float) -> float:
        """Move subtitles `delta` seconds later (negative: earlier) and return
        the offset now in force. For a subtitle timed to another release of
        the episode, where a shift is the whole fix."""
        if self._player is None:
            return 0.0
        offset = round(self._player.subtitle_delay() + delta, 2)
        self._player.set_subtitle_delay(offset)
        return offset

    @Slot(int)
    def selectSubtitle(self, track_id: int) -> None:
        if self._player is None:
            return
        self._subtitle_picks += 1
        if track_id >= self.ONLINE_SUBTITLE_ID:
            menu = self._online_menu()
            index = track_id - self.ONLINE_SUBTITLE_ID
            if 0 <= index < len(menu):
                self._picking = menu[index]
                self.subtitleTracksChanged.emit()
                self._spawn(self._load_online(menu[index], self._subtitle_picks))
            return
        self._picking = None
        self._shown_online = None
        self._subtitle_choice = None if track_id < 0 else track_id
        self._player.set_subtitle_track(self._subtitle_choice)
        self.subtitleTracksChanged.emit()

    @Slot(int)
    def selectAudio(self, track_id: int) -> None:
        if self._player is None:
            return
        self._player.set_audio_track(None if track_id < 0 else track_id)
        self._refresh_media_format()

    def _refresh_media_format(self, fmt: MediaFormat | None = None) -> None:
        if fmt is None:
            fmt = self._player.media_format() if self._player is not None else MediaFormat()
        if fmt != self._media_format:
            self._media_format = fmt
            self.mediaFormatChanged.emit()

    @Property("QVariantList", notify=mediaFormatChanged)  # type: ignore[arg-type]
    def mediaBadges(self) -> list[dict[str, str]]:
        """The playing file's formats, as badges: `format` (which mark to
        show: dolby-vision, hdr10-plus, hdr10, hlg, 4k, 1440p, 1080p, 720p,
        dolby-atmos, dts-x, channels), `label` (its words, for the channel
        count and where no mark exists) and `detail` for the hover. What the
        file is, never what the addon's label claimed, and the source rather
        than the screen -- an HDR file on an SDR display is tone-mapped, and
        says so."""
        fmt = self._media_format
        badges: list[dict[str, str]] = []
        if fmt.hdr:
            what = (
                f"Dolby Vision profile {fmt.dolby_vision_profile}"
                if fmt.hdr == "Dolby Vision"
                else fmt.hdr
            )
            badges.append(
                {
                    "format": fmt.hdr.lower().replace(" ", "-").replace("+", "-plus"),
                    "label": fmt.hdr,
                    "detail": f"{what} video, tone-mapped for a standard-range display.",
                }
            )
        if fmt.resolution:
            badges.append(
                {
                    "format": fmt.resolution.lower(),
                    "label": fmt.resolution,
                    "detail": f"{fmt.resolution} video.",
                }
            )
        if fmt.immersive_audio:
            badges.append(
                {
                    "format": fmt.immersive_audio.lower().replace(" ", "-").replace(":", "-"),
                    "label": fmt.immersive_audio,
                    "detail": f"{fmt.immersive_audio} on the selected audio track.",
                }
            )
        if fmt.channels:
            badges.append(
                {
                    "format": "channels",
                    "label": fmt.channels,
                    "detail": f"{fmt.channels} channels on the selected audio track.",
                }
            )
        return badges

    @Slot(result=int)
    def currentSubtitle(self) -> int:
        if self._player is None:
            return -1
        if self._picking is not None:
            menu = self._online_menu()
            if self._picking in menu:
                return self.ONLINE_SUBTITLE_ID + menu.index(self._picking)
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
        tracks: list[dict[str, object]] = [
            {"id": tid, "title": title} for tid, title in self._player.subtitle_tracks()
        ]
        tracks.extend(
            {
                "id": self.ONLINE_SUBTITLE_ID + index,
                "title": self._online_title(s)
                + (" (loading\u2026)" if s is self._picking else " (Online)"),
            }
            for index, s in enumerate(self._online_menu())
        )
        return tracks

    @Slot(result="QVariantList")
    def audioTracks(self) -> list[dict[str, object]]:
        if self._player is None:
            return []
        return [{"id": tid, "title": title} for tid, title in self._player.audio_tracks()]
