"""Port interfaces (Protocols) the application depends on and infrastructure implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)


@runtime_checkable
class AddonSource(Protocol):
    async def fetch_manifest(self, url: str) -> AddonManifest: ...
    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]: ...
    async def fetch_meta(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> MetaDetail: ...
    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]: ...


@runtime_checkable
class Cache(Protocol):
    async def get_or_fetch(self, url: str) -> bytes: ...


@runtime_checkable
class MediaPlayer(Protocol):
    def play(self, url: str) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def set_subtitle_track(self, track_id: int | None) -> None: ...
    def subtitle_tracks(self) -> list[tuple[int, str]]: ...
    def shutdown(self) -> None: ...


@runtime_checkable
class DebridResolver(Protocol):
    """Later milestone: resolve an infoHash stream to a direct URL. Unused in MVP."""

    async def resolve(self, stream: Stream) -> str: ...
