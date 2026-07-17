"""Port interfaces (Protocols) the application depends on and infrastructure implements."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    PersistedSettings,
    PlaybackProgress,
    Ratings,
    ResolvedMedia,
    Stream,
    SubtitleStyle,
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


@runtime_checkable
class MediaPlayer(Protocol):
    def play(
        self,
        url: str,
        *,
        start: float = 0.0,
        headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Begin playback, seeking to `start` seconds at load time.

        `headers` are the stream's behaviorHints.proxyHeaders.request: some
        addons serve their URL only with a specific Referer/User-Agent.
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

    def buffered_to(self) -> float:
        """Absolute position (seconds) the demuxer has downloaded up to, or 0.0
        when unknown — drives the loaded-ahead track in the timeline."""
        ...

    def set_subtitle_track(self, track_id: int | None) -> None: ...
    def subtitle_tracks(self) -> list[tuple[int, str]]: ...
    def set_audio_track(self, track_id: int | None) -> None: ...
    def audio_tracks(self) -> list[tuple[int, str]]: ...
    def current_subtitle_track(self) -> int | None: ...
    def current_audio_track(self) -> int | None: ...
    def apply_subtitle_style(self, style: SubtitleStyle) -> None: ...
    def set_tracks_changed_callback(self, callback: Callable[[], None] | None) -> None: ...
    def set_state_changed_callback(self, callback: Callable[[], None] | None) -> None: ...
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

    def delete_many(self, keys: list[tuple[str, str]]) -> None:
        """Remove exactly these (media_id, video_id) rows, in one transaction.
        Pruning deletes thousands at once; one round-trip each would stall
        startup for tens of seconds."""
        ...

    def clear(self) -> None: ...
