"""Port interfaces (Protocols) the application depends on and infrastructure implements."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    DecodeReport,
    MediaFormat,
    MediaItem,
    MediaType,
    MetaDetail,
    PersistedSettings,
    PlaybackProgress,
    Ratings,
    ResolvedMedia,
    Segments,
    Stream,
    Subtitle,
    SubtitleStyle,
    TrackLanguages,
    TraktAuth,
    TraktDeviceCode,
    TraktHistoryItem,
    TraktListItem,
    TraktPlayback,
    WatchlistEntry,
)


@runtime_checkable
class AddonSource(Protocol):
    async def fetch_manifest(self, url: str) -> AddonManifest: ...
    async def fetch_catalog(
        self,
        manifest: AddonManifest,
        ref: CatalogRef,
        *,
        genre: str | None = None,
        skip: int = 0,
        search: str | None = None,
    ) -> list[MediaItem]: ...
    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail: ...
    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]: ...
    async def fetch_subtitles(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Subtitle]: ...
    async def stored_meta(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> MetaDetail | None:
        """The last fetch_meta answer kept on disk, of any age; None when there
        is none."""
        ...

    async def stored_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream] | None:
        """The last fetch_streams answer kept on disk, links possibly expired;
        None when there is none."""
        ...


@runtime_checkable
class MediaPlayer(Protocol):
    def play(
        self,
        url: str,
        *,
        start: float = 0.0,
        headers: Sequence[tuple[str, str]] = (),
        upstream_cached: bool = False,
    ) -> None:
        """Begin playback, seeking to `start` seconds at load time.

        `headers` are the stream's behaviorHints.proxyHeaders.request: some
        addons serve their URL only with a specific Referer/User-Agent.
        `upstream_cached` says `url` is a local proxy that keeps the stream
        cached on disk itself, so the player's own cache can stay small.
        """
        ...

    def stop(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def is_paused(self) -> bool: ...
    def seek(self, seconds: float) -> None: ...
    def position(self) -> float: ...
    def duration(self) -> float: ...
    def set_volume(self, volume: float) -> None: ...
    def volume(self) -> float: ...
    def set_muted(self, muted: bool) -> None: ...
    def is_muted(self) -> bool: ...
    def is_loading(self) -> bool:
        """True while playback is stalled on I/O (buffering or seeking)."""
        ...

    def video_dolby_vision_profile(self) -> int:
        """Dolby Vision profile of the selected video track, 0 when the track
        carries no DV metadata or nothing is loaded yet."""
        ...

    def chapters(self) -> list[tuple[float, str]]:
        """The file's chapters as (start seconds, title), in order; empty when
        it has none or nothing is loaded yet."""
        ...

    def media_format(self) -> MediaFormat:
        """What the playing file is (HDR format, resolution, immersive audio,
        channels), from its selected tracks; empty before a file is open."""
        ...

    def decode_report(self) -> DecodeReport:
        """What decoding this file has cost so far: codec, frame height and the
        frames dropped since it loaded. An empty report (no codec, no height)
        means nothing is loaded yet and nothing can be concluded.
        """
        ...

    def download_speed(self) -> float:
        """Current read speed from the network, in bytes per second, or 0.0
        when unknown (a local file, or nothing loaded yet)."""
        ...

    def buffered_to(self) -> float:
        """Absolute position (seconds) the demuxer has downloaded up to, or 0.0
        when unknown — drives the loaded-ahead track in the timeline."""
        ...

    def video_size(self) -> tuple[int, int]:
        """Decoded frame size (width, height), or (0, 0) when nothing is
        decoded yet — the PiP tile falls back to 16:9 until it is known."""
        ...

    def prepare_track_switching(self) -> None:
        """Called once the track list is known, on the GUI thread, so the
        player may arrange for later track changes to be cheap. Playing
        without it must still work — it is an optimisation, not a step."""
        ...

    def set_subtitle_track(self, track_id: int | None) -> None: ...

    def set_subtitle_delay(self, seconds: float) -> None:
        """Shift subtitles later (positive) or earlier (negative) against the
        picture. Reset to 0 with each new file."""
        ...

    def subtitle_delay(self) -> float: ...

    def choose_fallback_subtitle(self) -> bool:
        """With a subtitle language preferred and nothing chosen by language
        tag, choose an untagged track whose title names that language, or the
        only subtitle track there is. True if it chose one."""
        ...

    def has_preferred_subtitle(self) -> bool:
        """Whether the file itself carries a full (not forced-only) subtitle
        track in the preferred language."""
        ...

    def add_subtitle(self, url: str, title: str, lang: str, *, select: bool = True) -> None:
        """Load a subtitle file beside the video, shown at once if `select`.
        Blocks while the file downloads: call it off the GUI thread."""
        ...

    def subtitle_tracks(self) -> list[tuple[int, str]]: ...
    def set_audio_track(self, track_id: int | None) -> None: ...
    def audio_tracks(self) -> list[tuple[int, str]]: ...
    def current_subtitle_track(self) -> int | None: ...
    def current_audio_track(self) -> int | None: ...
    def apply_subtitle_style(self, style: SubtitleStyle) -> None: ...

    def set_track_languages(self, languages: TrackLanguages) -> None:
        """Prefer these languages when the NEXT file is loaded; the file that
        is playing keeps its tracks. Call it before each new file, not before
        a reload of the same one: a play() with no call before it is taken to
        be a reload (a reconnect mid-episode), and keeps the tracks the viewer
        switched to rather than going back to the preference."""
        ...

    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None: ...
    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None: ...

    def set_stream_ended_callback(self, callback: Callable[[], None] | None) -> None:
        """Called when playback stops feeding frames — a finished file, or a
        connection that died and was reported as end of file."""
        ...

    def set_opened_callback(self, callback: Callable[[], None] | None) -> None:
        """Called once per loaded URL, when the player has it open (connected,
        header read). May arrive on the player's own thread. Its absence is
        what gives away a host that accepted the connection and then said
        nothing: nothing fails, so no other callback ever fires."""
        ...

    def set_load_failed_callback(self, callback: Callable[[], None] | None) -> None:
        """Called when a URL never opened at all.

        A separate signal because it is a separate event: a stream that dies
        halfway is reported as the file ending, while one whose host refuses
        the connection produces no end of file to notice -- the player simply
        never starts, and without this nothing above ever hears about it.
        """
        ...

    def render_handle(self) -> object | None:
        """Opaque native handle for an in-scene video renderer (None for fakes)."""
        ...

    def shutdown(self) -> None: ...


@runtime_checkable
class DebridResolver(Protocol):
    """Later milestone: resolve an infoHash stream to a direct URL. Unused in MVP."""

    async def resolve(self, stream: Stream) -> str: ...


@runtime_checkable
class ExternalIdResolver(Protocol):
    async def resolve(self, source: str, external_id: str) -> ResolvedMedia: ...


@runtime_checkable
class RatingsResolver(Protocol):
    async def ratings(self, imdb_id: str, media_type: MediaType) -> Ratings: ...


@runtime_checkable
class TraktApi(Protocol):
    """Trakt's HTTP surface, verb by verb. Token lifecycle (when to refresh,
    where tokens live) is application policy and stays out of here. Every
    call may raise TraktError."""

    async def device_code(self, client_id: str) -> TraktDeviceCode: ...
    async def poll_device_token(
        self, client_id: str, client_secret: str, device_code: str
    ) -> TraktAuth | None:
        """One poll. None while the user has not approved yet; raises
        TraktError once the code is denied or expired."""
        ...

    async def refresh_token(
        self, client_id: str, client_secret: str, refresh_token: str
    ) -> TraktAuth: ...
    async def revoke(self, client_id: str, client_secret: str, access_token: str) -> None: ...
    async def username(self, client_id: str, access_token: str) -> str: ...
    async def scrobble(
        self,
        client_id: str,
        access_token: str,
        action: str,
        *,
        imdb_id: str,
        season: int | None,
        episode: int | None,
        progress: float,
    ) -> None:
        """action is "start" | "pause" | "stop". season/episode None means a
        movie; set means an episode of the show `imdb_id`."""
        ...

    async def playback(self, client_id: str, access_token: str) -> list[TraktPlayback]:
        """The user's paused-playback list, newest first — what other Trakt
        clients left unfinished."""
        ...

    async def remove_playback(self, client_id: str, access_token: str, playback_id: int) -> None:
        """Delete one paused-playback row (already-gone is not an error)."""
        ...

    async def recommendations(
        self, client_id: str, access_token: str, media_type: MediaType, limit: int
    ) -> list[TraktListItem]:
        """Trakt's personalized recommendations for one media type."""
        ...

    async def history(
        self, client_id: str, access_token: str, limit: int
    ) -> list[TraktHistoryItem]:
        """The user's watch history, newest play first — one item per play,
        so the same title may repeat. Episode plays carry the show's imdb id
        plus season/episode."""
        ...

    async def add_to_history(
        self,
        client_id: str,
        access_token: str,
        *,
        media_type: MediaType,
        imdb_id: str,
        season: int | None,
        episode: int | None,
    ) -> None:
        """Mark watched now. "movie" is the film `imdb_id`; "series" with
        season/episode is one episode of that show, without them the whole
        show (Trakt marks every episode)."""
        ...


@runtime_checkable
class BandwidthProbe(Protocol):
    """One-shot measurement of downstream bandwidth against a URL the user was
    about to stream from anyway. Implementations must never raise: an
    unreachable host, a server that ignores Range, or a timeout all mean "no
    measurement", which is None."""

    async def measure_kbps(
        self, url: str, headers: Sequence[tuple[str, str]] = ()
    ) -> int | None: ...


class LinkResolver(Protocol):
    """Follow a stream URL's redirects to where the bytes actually are.

    An addon's stream URL is usually a signing endpoint: asked for the file,
    it resolves a debrid link and redirects there, which is a round trip
    playback otherwise pays after the click. Must never raise; None means
    there was nothing to gain (no redirect, or no answer)."""

    async def resolve(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str | None: ...


class FloatingWindow(Protocol):
    """The desktop's side of picture-in-picture: keep this app's window above
    every other and without a title bar while `floating`, and give it back
    both when not. Window flags already ask for this; this is for desktops
    that do not honour flags set on a live window. Must never raise: a desktop
    that cannot do it leaves the tile an ordinary window, which still works."""

    def set_floating(self, floating: bool) -> None: ...


class SegmentSource(Protocol):
    """Where an episode's intro, recap and credits are, from somewhere other
    than the file -- a crowd-sourced timestamp database. Only asked when the
    file's own chapters do not name them. `duration` is the file's length, so
    an answer recorded against another cut of the episode can be lined up
    with this one. None when nothing is known (or the source cannot be
    reached); must not raise."""

    async def segments(
        self, imdb_id: str, season: int, episode: int, duration: float
    ) -> Segments | None: ...


class IdleInhibitor(Protocol):
    """Keeps the screen on and the machine awake while `inhibited`: a video
    playing is a user who is watching, not an idle session, and nothing else
    tells the desktop so -- libmpv renders into our scene, not a window of its
    own, so mpv's own screensaver handling never engages. Idempotent, returns
    at once, and must never raise: a desktop that cannot be asked leaves the
    screen to dim as before, and playback is untouched."""

    def set_inhibited(self, inhibited: bool) -> None: ...


@runtime_checkable
class StreamAccelerator(Protocol):
    """Something that can pull one stream over several connections at once and
    serve it back locally.

    `local_url` must be synchronous and must never raise: playback calls it on
    the GUI thread, and a URL it cannot help with is answered by returning that
    URL unchanged. That is what makes the accelerator removable without the
    player knowing it existed.
    """

    def local_url(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str: ...

    def holds_stream_cache(self) -> bool:
        """Whether a stream served through `local_url` is cached on disk by the
        accelerator itself, so the player may keep only a small cache of its
        own instead of a second copy of the same bytes."""
        ...

    def upstream_bytes_per_s(self) -> float:
        """Bytes per second being pulled from the real host, or 0.0 when
        nothing has been fetched lately. Zero means "nothing to report", and
        for a proxied stream the caller records no sample at all: the player's
        own reading would be measuring localhost."""
        ...

    def last_failure(self) -> str | None:
        """Why the most recent stream could not be reached, in a form fit to
        show a viewer, or None when nothing has failed.

        Something has to name the host. A stream URL is a signing endpoint
        that redirects to a CDN node, so every failure the player can see says
        "the addon's domain", and the host that actually refused only exists
        in here. Without it a dead debrid node and a broken app look the same
        from the sofa.
        """
        ...


@runtime_checkable
class SettingsStore(Protocol):
    """Durable store for user settings. load() must never raise on missing or
    corrupt data — it returns defaults instead."""

    def load(self) -> PersistedSettings: ...
    def save(self, settings: PersistedSettings) -> None: ...


@runtime_checkable
class WatchlistStore(Protocol):
    """Durable store for the user's watchlist. load_all() must never raise on
    missing or corrupt data — it returns an empty list instead."""

    def load_all(self) -> list[WatchlistEntry]: ...
    def save(self, entry: WatchlistEntry) -> None: ...
    def delete(self, media_id: str) -> None: ...
    def clear(self) -> None: ...


@runtime_checkable
class ProgressStore(Protocol):
    """Durable store for playback progress. load_all() must never raise on
    missing or corrupt data — it returns an empty list instead."""

    def load_all(self) -> list[PlaybackProgress]: ...
    def save(self, entry: PlaybackProgress) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None:
        """video_id None removes every entry for the media (a whole series)."""
        ...

    def load_forgotten(self) -> list[tuple[str, str, int]]:
        """Every forget tombstone as (media_id, video_id, deleted_at).
        video_id "*" marks a whole-media forget. Must never raise — missing
        or corrupt data returns an empty list."""
        ...

    def save_forgotten(self, media_id: str, video_id: str, deleted_at: int) -> None:
        """Record (or refresh) one forget tombstone."""
        ...

    def delete_many(self, keys: list[tuple[str, str]]) -> None:
        """Remove exactly these (media_id, video_id) rows, in one transaction.
        Pruning deletes thousands at once; one round-trip each would stall
        startup for tens of seconds."""
        ...

    def clear(self) -> None: ...
