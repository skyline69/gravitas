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
    year: str | None = None
    imdb_rating: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    imdb_id: str
    type: MediaType
    name: str
    poster: str | None
    year: str | None


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
    logo: str | None = None
    year: str | None = None
    runtime: str | None = None
    imdb_rating: str | None = None
    genres: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()


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
    genres: tuple[str, ...] = ()
    supports_skip: bool = False
    supports_search: bool = False


@dataclass(frozen=True, slots=True)
class AddonManifest:
    id: str
    name: str
    version: str
    resources: tuple[str, ...]
    types: tuple[str, ...]
    catalogs: tuple[CatalogRef, ...]
    base_url: str


@dataclass(frozen=True, slots=True)
class PersistedSettings:
    """User state restored across launches. Protected (built-in) addons are
    re-installed by bootstrap and never persisted."""

    addon_urls: tuple[str, ...] = ()
    tmdb_key: str | None = None
