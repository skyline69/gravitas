"""One playback on a cast target, as the player sees it.

A TV is a remote player: every command is a SOAP round trip, and what it is
doing is only known by asking. The player page polls position twice a second
and expects an answer at once, so the session keeps the TV's last report and
answers from it, asks again every POLL_S, and sends commands in the
background. A command's effect is shown immediately (a pause pauses, a seek
moves the timeline) and the next report either confirms it or puts the truth
back -- the TV's own remote is just as able to change things.

The TV fetches the media itself, through a relay on this machine (see
StreamRelay): an addon's URL usually needs headers the TV cannot send, or a
redirect to an HTTPS CDN it may not follow. So the relay is the session's,
started with it and closed with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit

from gravitas.domain.errors import CastFailed
from gravitas.domain.models import CastStatus
from gravitas.domain.ports import CastTarget, StreamRelay

_log = logging.getLogger(__name__)

# What a file's extension says it is. Anything else is sent as Matroska: it
# is what debrid releases nearly always are, and a TV that cannot play the
# file says so whatever it was told.
_MIME_BY_EXTENSION = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
}
DEFAULT_MIME = "video/x-matroska"


def cast_mime(url: str, filename: str = "") -> str:
    """The MIME type to announce for a stream: by the release's file name if
    the addon gave one, else by the URL's path (a signing endpoint's path says
    nothing, which is why the file name comes first)."""
    for name in (filename, urlsplit(url).path):
        dot = name.rfind(".")
        if dot != -1:
            mime = _MIME_BY_EXTENSION.get(name[dot:].lower())
            if mime is not None:
                return mime
    return DEFAULT_MIME


def uncastable_reason(url: str) -> str | None:
    """Why `url` cannot be sent to a TV, or None if it can. A TV plays a file
    at an HTTP URL; it has no yt-dlp to resolve a YouTube page, and an HLS
    playlist names segments the relay would have to rewrite."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "This source can't be sent to a TV."
    host = (parts.hostname or "").lower()
    if host.endswith("youtube.com") or host.endswith("youtu.be"):
        return "YouTube videos can't be sent to a TV."
    if parts.path.lower().endswith(".m3u8"):
        return "Live-streaming (HLS) sources can't be sent to a TV."
    return None


class CastSession:
    POLL_S = 1.0
    # Volume changes are rare and cost a round trip of their own.
    VOLUME_EVERY_POLLS = 5
    # Unanswered polls in a row before the TV counts as gone (switched off,
    # off the network). Seconds, at POLL_S.
    LOST_AFTER_POLLS = 8
    # How long a load may report "stopped" before the TV has played anything.
    # Renderers report STOPPED between being handed a URL and opening it, for
    # as long as opening takes -- seconds through a debrid redirect.
    OPEN_GRACE_S = 25.0
    # A resume point closer to the start than this is played from the start.
    START_SEEK_MIN_S = 5.0
    # After a seek, reports this far from its target are the TV catching up,
    # and the target is shown instead -- for at most SEEK_HOLD_S.
    SEEK_TOLERANCE_S = 3.0
    SEEK_HOLD_S = 8.0
    NEVER_PLAYED = "The TV couldn't play this source. It may not support the format."
    LOST = "Lost contact with the TV."

    def __init__(
        self,
        target: CastTarget,
        relay: StreamRelay,
        *,
        on_changed: Callable[[], None],
        on_ended: Callable[[], None],
        on_failed: Callable[[str], None],
        on_error: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._target = target
        self._relay = relay
        self._on_changed = on_changed
        self._on_ended = on_ended
        self._on_failed = on_failed
        self._on_error = on_error
        self._clock = clock
        self._status = CastStatus(state="loading")
        self._duration_hint = 0.0
        self._last_position = 0.0
        self._volume = 100
        self._muted = False
        self._played = False
        self._loaded_at = 0.0
        self._pending_seek = 0.0
        self._seek_target: float | None = None
        self._seek_at = 0.0
        # Bumped per load: a report or a callback for an earlier file is
        # dropped, not applied to this one.
        self._load_token = 0
        self._poll: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._closed = False

    # --- what the player reads ---

    @property
    def name(self) -> str:
        return self._target.name

    @property
    def paused(self) -> bool:
        return self._status.state == "paused"

    @property
    def loading(self) -> bool:
        return self._status.state == "loading" or self._seek_target is not None

    @property
    def volume(self) -> float:
        return float(self._volume)

    @property
    def muted(self) -> bool:
        return self._muted

    def position(self) -> float:
        if self._seek_target is not None:
            return self._seek_target
        return self._last_position

    def duration(self) -> float:
        return self._status.duration or self._duration_hint

    # --- lifecycle ---

    async def start(self) -> None:
        await self._relay.start()
        with contextlib.suppress(CastFailed):
            self._volume = await self._target.volume()

    async def load(
        self,
        url: str,
        headers: tuple[tuple[str, str], ...],
        *,
        title: str,
        mime: str,
        start: float = 0.0,
        duration: float = 0.0,
    ) -> None:
        """Hand the TV a new file. Raises CastFailed if it refuses it outright;
        a file it accepts and then cannot play is reported to on_failed."""
        self._load_token += 1
        self._played = False
        self._loaded_at = self._clock()
        self._duration_hint = duration
        self._last_position = start
        self._seek_target = None
        self._pending_seek = start if start >= self.START_SEEK_MIN_S else 0.0
        self._status = CastStatus(state="loading", position=start)
        local = self._relay.local_url(url, headers, content_type=mime)
        _log.info("casting %s to %s as %s", title or "?", self._target.name, mime)
        await self._target.play(local, mime=mime, title=title)
        if self._poll is None and not self._closed:
            self._poll = asyncio.get_running_loop().create_task(self._poll_forever())

    async def close(self, *, stop_target: bool) -> None:
        """End the session. The TV is told to stop only when the viewer ended
        it here: a TV that stopped by itself needs no telling, and one that is
        gone would cost a timeout."""
        if self._closed:
            return
        self._closed = True
        if self._poll is not None:
            self._poll.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._poll
            self._poll = None
        if stop_target:
            try:
                await self._target.stop()
            except CastFailed as exc:
                _log.info("could not stop %s: %s", self._target.name, exc)
        await self._relay.aclose()

    # --- commands ---

    def pause(self) -> None:
        self._status = CastStatus("paused", self._status.position, self._status.duration)
        self._send(self._target.pause())

    def resume(self) -> None:
        self._status = CastStatus("playing", self._status.position, self._status.duration)
        self._send(self._target.resume())

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        duration = self.duration()
        if duration > 0:
            seconds = min(seconds, duration)
        self._seek_target = seconds
        self._seek_at = self._clock()
        self._last_position = seconds
        self._send(self._target.seek(seconds))

    def set_volume(self, volume: float) -> None:
        self._volume = max(0, min(100, round(volume)))
        self._send(self._target.set_volume(self._volume))

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self._send(self._target.set_muted(muted))

    def _send(self, command: Coroutine[Any, Any, None]) -> None:
        if self._closed:
            command.close()
            return
        task = asyncio.get_running_loop().create_task(self._run(command))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _run(self, command: Coroutine[Any, Any, None]) -> None:
        try:
            await command
        except CastFailed as exc:
            # The next report puts the timeline and the play button back.
            self._seek_target = None
            self._on_error(str(exc))

    # --- reports ---

    async def _poll_forever(self) -> None:
        unanswered = 0
        polls = 0
        while not self._closed:
            token = self._load_token
            try:
                status = await self._target.status()
            except CastFailed as exc:
                unanswered += 1
                _log.debug("no report from %s (%d): %s", self._target.name, unanswered, exc)
                if unanswered >= self.LOST_AFTER_POLLS:
                    self._on_failed(self.LOST)
                    return
            else:
                unanswered = 0
                if token == self._load_token and self._apply(status):
                    return
                # Apart from the report: a renderer with no volume control
                # is still a renderer that answers.
                if polls % self.VOLUME_EVERY_POLLS == 0:
                    with contextlib.suppress(CastFailed):
                        self._volume = await self._target.volume()
            polls += 1
            await asyncio.sleep(self.POLL_S)

    def _apply(self, status: CastStatus) -> bool:
        """Take a report in. True when it ends the session's playback."""
        now = self._clock()
        if status.state == "stopped":
            if self._played:
                _log.info("%s stopped at %.0fs", self._target.name, self._last_position)
                self._on_ended()
                return True
            if now - self._loaded_at > self.OPEN_GRACE_S:
                self._on_failed(self.NEVER_PLAYED)
                return True
            return False  # still opening; the "loading" shown is the truth
        # Before the resume seek below, which is clamped to the duration this
        # report is the first to state.
        self._status = status
        if status.state == "playing":
            self._played = True
            if self._pending_seek > 0:
                self.seek(self._pending_seek)
                self._pending_seek = 0.0
        if self._seek_target is not None:
            near = abs(status.position - self._seek_target) <= self.SEEK_TOLERANCE_S
            if near or now - self._seek_at > self.SEEK_HOLD_S:
                self._seek_target = None
        if self._seek_target is None and status.state != "loading" and self._pending_seek == 0:
            self._last_position = status.position
        self._on_changed()
        return False
