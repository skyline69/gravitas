"""Pure domain entities. Frozen dataclasses, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MediaType = Literal["movie", "series"]


@dataclass(frozen=True, slots=True)
class MediaItem:
    id: str
    type: MediaType
    name: str
    poster: str | None


@dataclass(frozen=True, slots=True)
class Video:
    id: str
    title: str
    season: int | None
    episode: int | None


@dataclass(frozen=True, slots=True)
class MetaDetail:
    id: str
    type: MediaType
    name: str
    description: str | None
    poster: str | None
    background: str | None
    videos: tuple[Video, ...]


@dataclass(frozen=True, slots=True)
class Stream:
    name: str
    title: str
    url: str | None
    info_hash: str | None
    file_idx: int | None

    @property
    def is_direct(self) -> bool:
        return self.url is not None


@dataclass(frozen=True, slots=True)
class CatalogRef:
    type: MediaType
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class AddonManifest:
    id: str
    name: str
    version: str
    resources: tuple[str, ...]
    types: tuple[str, ...]
    catalogs: tuple[CatalogRef, ...]
    base_url: str
