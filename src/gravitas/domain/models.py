"""Pure domain entities. Frozen dataclasses, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The protocol also defines "channel" and "tv". Supporting them would make
# Gravitas an IPTV client -- a product decision, not a parsing one -- so
# catalogs of those types are dropped at parse time. See parse_manifest.
MediaType = Literal["movie", "series"]

# How an addon wants its artwork framed. "poster" (2:3) is the protocol default.
PosterShape = Literal["poster", "landscape", "square"]


@dataclass(frozen=True, slots=True)
class MediaItem:
    id: str
    type: MediaType
    name: str
    poster: str | None
    year: str | None = None
    imdb_rating: str | None = None
    poster_shape: PosterShape = "poster"


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
class Ratings:
    """External critic/audience scores for a title, keyed by its IMDb id.

    All optional: a missing source is None and renders nothing. IMDb is NOT
    here — it comes from the addon's own meta. `rotten_tomatoes` is a percent
    string with no sign (e.g. "87"); `letterboxd` is out of 5 (e.g. "4.1")."""

    rotten_tomatoes: str | None = None
    rotten_tomatoes_fresh: bool | None = None
    letterboxd: str | None = None


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
    writers: tuple[str, ...] = ()
    poster_shape: PosterShape = "poster"
    # trailerStreams[0].ytId, falling back to the legacy trailers[0].source.
    trailer_yt_id: str | None = None
    # behaviorHints.defaultVideoId: the video whose streams represent the title
    # itself. Cinemeta sets it to the imdb id for movies, null for series.
    default_video_id: str | None = None


def _youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


@dataclass(frozen=True, slots=True)
class Stream:
    name: str
    title: str
    url: str | None
    info_hash: str | None
    file_idx: int | None
    yt_id: str | None = None
    external_url: str | None = None
    # behaviorHints.proxyHeaders.request: some addons only serve their URL with
    # a specific Referer/User-Agent and 403 without it.
    proxy_headers: tuple[tuple[str, str], ...] = ()

    @property
    def playable_url(self) -> str | None:
        """What the player can be handed, or None if nothing here is playable.

        ytId goes through mpv's ytdl_hook, which needs yt-dlp on PATH -- an
        optional system dependency, like libmpv itself.
        """
        if self.url:
            return self.url
        if self.yt_id:
            return _youtube_url(self.yt_id)
        return None

    @property
    def is_direct(self) -> bool:
        """Playable in-app. infoHash-only streams are deliberately never direct:
        Gravitas has no torrent engine (see CLAUDE.md)."""
        return self.playable_url is not None

    @property
    def is_external(self) -> bool:
        """Nothing playable, but a URL a browser can open."""
        return self.playable_url is None and bool(self.external_url)


@dataclass(frozen=True, slots=True)
class ExtraSpec:
    """One entry of a catalog's `extra` array."""

    name: str
    is_required: bool = False
    options: tuple[str, ...] = ()
    # optionsLimit: how many options a user may select at once. Parsed for
    # conformance; the genre UI is single-select, so nothing reads it yet.
    options_limit: int = 1


# Extras Gravitas can satisfy on its own when an addon demands them. Anything
# else required (lastVideosIds, calendarVideosIds -- both Cinemeta) describes a
# catalog driven by the user's library, which this app cannot supply.
_SATISFIABLE_EXTRAS = frozenset({"genre", "skip"})


@dataclass(frozen=True, slots=True)
class CatalogRef:
    type: MediaType
    id: str
    name: str
    extra: tuple[ExtraSpec, ...] = ()

    def _extra(self, name: str) -> ExtraSpec | None:
        for spec in self.extra:
            if spec.name == name:
                return spec
        return None

    @property
    def genres(self) -> tuple[str, ...]:
        spec = self._extra("genre")
        return spec.options if spec is not None else ()

    @property
    def supports_skip(self) -> bool:
        return self._extra("skip") is not None

    @property
    def supports_search(self) -> bool:
        return self._extra("search") is not None

    @property
    def required_extras(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.extra if spec.is_required)

    @property
    def requires_genre(self) -> bool:
        spec = self._extra("genre")
        return spec is not None and spec.is_required

    @property
    def is_browsable(self) -> bool:
        """Can this catalog be listed without user input beyond a genre pick?

        A required `genre` is satisfiable only if the addon told us the options
        to choose from; a required `search` makes the catalog search-only.
        """
        for name in self.required_extras:
            if name not in _SATISFIABLE_EXTRAS:
                return False
        return not self.requires_genre or bool(self.genres)


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """One entry of a manifest's `resources`.

    The protocol allows either a bare name ("stream") or an object narrowing it
    ({"name": "stream", "types": ["movie"], "idPrefixes": ["tt"]}). Empty tuples
    mean "inherit the manifest's own types/idPrefixes".
    """

    name: str
    types: tuple[str, ...] = ()
    id_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AddonBehaviorHints:
    """A manifest's `behaviorHints`: what the addon says about itself."""

    adult: bool = False
    p2p: bool = False
    configurable: bool = False
    # The addon cannot serve anything until configured on its own web page.
    # Installing one regardless yields an addon that silently returns nothing.
    configuration_required: bool = False


@dataclass(frozen=True, slots=True)
class AddonManifest:
    id: str
    name: str
    version: str
    resources: tuple[ResourceSpec, ...]
    types: tuple[str, ...]
    catalogs: tuple[CatalogRef, ...]
    base_url: str
    id_prefixes: tuple[str, ...] = ()
    description: str | None = None
    logo: str | None = None
    behavior_hints: AddonBehaviorHints = AddonBehaviorHints()

    @property
    def configure_url(self) -> str:
        """The addon's own configuration page."""
        return self.base_url + "configure"

    def _resource(self, resource: str) -> ResourceSpec | None:
        for spec in self.resources:
            if spec.name == resource:
                return spec
        return None

    @property
    def resource_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.resources)

    def serves(self, resource: str, type: MediaType | None = None, id: str | None = None) -> bool:
        """Would this addon answer such a request? Asking one that would not is
        a guaranteed 404 -- a round-trip of latency and a log line per call.

        An empty types/idPrefixes list means "no filter", per protocol. The
        idPrefixes filter deliberately does not apply to catalogs: the spec
        exempts them, and a catalog id is not a media id.
        """
        spec = self._resource(resource)
        if spec is None:
            return False
        if type is not None:
            types = spec.types or self.types
            if types and type not in types:
                return False
        if id is not None and resource != "catalog":
            prefixes = spec.id_prefixes or self.id_prefixes
            if prefixes and not any(id.startswith(p) for p in prefixes):
                return False
        return True


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
    mdblist_key: str | None = None
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
