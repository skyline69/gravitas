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
    thumbnail: str | None = None
    overview: str | None = None
    released: str | None = None


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
class SubtitleStyle:
    """User-facing subtitle rendering preferences (mpv sub-* options)."""

    font_size: int = 55
    color: str = "#FFFFFF"
    border_size: int = 3
    back_opacity: int = 0  # 0-100 (%) black box behind the text
    bold: bool = False


@dataclass(frozen=True, slots=True)
class PersistedSettings:
    """User state restored across launches. Protected (built-in) addons are
    re-installed by bootstrap and never persisted."""

    addon_urls: tuple[str, ...] = ()
    tmdb_key: str | None = None
    subtitle_style: SubtitleStyle = SubtitleStyle()


@dataclass(frozen=True, slots=True)
class PlaybackProgress:
    """One resumable position. `video_id` is "" for movies.

    `name`, `poster` and `label` are denormalized onto the entry so the
    Settings list can render an item without refetching its meta.
    """

    media_id: str
    video_id: str
    type: MediaType
    name: str
    poster: str | None
    label: str
    position: float
    duration: float
    watched: bool
    updated_at: int

    @property
    def fraction(self) -> float:
        """0.0-1.0, for a progress bar."""
        if self.watched:
            # A watched entry has had its position zeroed, but reads as done.
            return 1.0
        if self.duration <= 0:
            return 0.0
        return min(1.0, self.position / self.duration)
