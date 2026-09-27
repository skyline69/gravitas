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
# The engine that plays video: Gravitas' own, or mpv (see PersistedSettings).
VideoPlayer = Literal["native", "mpv"]
VIDEO_PLAYERS: tuple[VideoPlayer, ...] = ("native", "mpv")


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
    # behaviorHints.filename: the release's own file name. The label an
    # aggregator shows often leaves out what this says (the episode's title,
    # the release group), and it is what tells two releases apart.
    filename: str = ""

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
    # "type:id" of each catalog the addon lists that Gravitas does not show
    # ("channel" and "tv" -- see MediaType). Kept so installing can say so: an
    # addon serving only those installs fine and shows nothing.
    ignored_catalogs: tuple[str, ...] = ()

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
class TrackLanguages:
    """The audio and subtitle language the viewer would rather have, as codes
    from domain/languages.py. "" is no preference -- whatever the file marks
    as its default -- and a subtitle of languages.SUBTITLES_OFF is none at
    all. A file without the preferred language plays its default, so a
    preference can make a playback better and never makes one fail."""

    audio: str = ""
    subtitle: str = ""


@dataclass(frozen=True, slots=True)
class Subtitle:
    """A subtitle file an addon offers for a video (the protocol's
    `subtitles` resource). Loaded whole by the player, beside the video, so
    switching to and between these never re-reads the stream."""

    url: str
    # As the addon tagged it: ISO 639-2 mostly ("eng"), sometimes the
    # OpenSubtitles variants ("pob"). See domain/languages.by_tag.
    lang: str
    # What tells two files in one language apart: the release or file name.
    label: str = ""
    addon: str = ""


@dataclass(frozen=True, slots=True)
class Segments:
    """Where an episode's intro, recap and end credits are, in seconds into
    the file; None where nothing says. Read off the file's own chapters
    first (application/segments.py), with a SegmentSource filling what they
    leave out."""

    intro: tuple[float, float] | None = None
    recap: tuple[float, float] | None = None
    # Where the end credits (or a closing preview) begin: the episode's story
    # is over, and the next one can be offered.
    credits_start: float | None = None


@dataclass(frozen=True, slots=True)
class TraktAuth:
    """A granted Trakt OAuth session. `expires_at` is wall-clock epoch seconds
    (created_at + expires_in from the token response); `username` is filled
    from /users/me after the grant and is display-only."""

    access_token: str
    refresh_token: str
    expires_at: int
    username: str = ""


@dataclass(frozen=True, slots=True)
class TraktDeviceCode:
    """One device-auth handshake: show `user_code` + `verification_url` to the
    user, poll every `interval` seconds for at most `expires_in`."""

    device_code: str
    user_code: str
    verification_url: str
    interval: int
    expires_in: int


@dataclass(frozen=True, slots=True)
class TraktPlayback:
    """One paused-playback entry from Trakt (/sync/playback) — what another
    client (Stremio, Kodi, …) scrobbled and left unfinished. `progress` is
    Trakt's percent (0-100); `runtime_minutes` comes from extended metadata
    and turns that percent back into seconds locally."""

    media_type: MediaType
    imdb_id: str
    title: str
    progress: float
    paused_at: int
    # Trakt's own row id — what DELETE /sync/playback/{id} wants when a
    # locally-forgotten title must stop coming back on the next sync.
    playback_id: int = 0
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None
    runtime_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class TraktHistoryItem:
    """One play from /sync/history — something the user finished, on any
    client. Episodes carry the SHOW's imdb id and title (Cinemeta-style
    ids address episodes as show:season:episode); movies leave
    season/episode None. `watched_at` orders it against local activity."""

    media_type: MediaType
    imdb_id: str
    title: str
    watched_at: int
    season: int | None = None
    episode: int | None = None
    episode_title: str | None = None


@dataclass(frozen=True, slots=True)
class TraktListItem:
    """One title from a Trakt list endpoint (recommendations, history).
    Trakt serves no artwork, so entries are bare references — posters come
    from the addons' own /meta, best-effort. History episodes collapse onto
    their show: a row of posters has nowhere to show S2E5."""

    media_type: MediaType
    imdb_id: str
    title: str


# The picture-in-picture tile stays a tile: narrower than this it cannot hold
# its own hover controls, wider it stops being picture-in-picture.
PIP_WIDTH_MIN = 240
PIP_WIDTH_MAX = 1280


# Transport the samples below were taken on. Bandwidth measured over a phone
# hotspot says nothing about the same laptop on ethernet, so samples are kept
# per bucket and never averaged across them. "unknown" is its own bucket, not
# a catch-all the others fall into: a platform with no transport backend has
# one bucket and behaves exactly like the naive design, which is correct for
# a machine that only ever has one connection.
TRANSPORT_BUCKETS = ("ethernet", "wifi", "cellular", "unknown")

# How many samples a bucket keeps. Old ones are dropped oldest-first.
CONNECTION_SAMPLE_CAP = 32
# Samples older than this describe a network the user may not even be on.
CONNECTION_SAMPLE_MAX_AGE_S = 14 * 24 * 60 * 60


# How much dropped-frame evidence is enough to say anything about a machine's
# decoding. Below this, a burst of drops while a seek settles would convict a
# codec the machine handles fine.
DECODE_OBSERVATION_MIN_S = 60.0
# Dropped frames per minute above which the machine is judged to be struggling
# with this codec at this height, and below which it is judged not to be.
# The gap between the two is deliberate: a bucket that lands inside it keeps
# whatever verdict it already had instead of flipping every other playback.
DECODE_STRAIN_DPM = 30.0
DECODE_SMOOTH_DPM = 10.0


@dataclass(frozen=True, slots=True)
class DecodeReport:
    """What the player observed about decoding the file it is playing.

    Every field comes from mpv, not from a label: `codec` is the decoder's own
    name for the track, `height` the frame it actually produced, and
    `dropped_frames` the count since the file loaded. An empty codec or a zero
    height means nothing is loaded yet, and nothing can be concluded.
    """

    codec: str = ""
    height: int = 0
    dropped_frames: int = 0


@dataclass(frozen=True, slots=True)
class ConnectionSample:
    """One observed download rate, in kbps, with when and where it was taken.

    Rates come from the player's own read speed during playback (and, once,
    from a probe against a stream host) -- Gravitas never contacts a speed-test
    service.
    """

    kbps: int
    at: int
    bucket: str = "unknown"


@dataclass(frozen=True, slots=True)
class PersistedSettings:
    """User state restored across launches. Protected (built-in) addons are
    re-installed by bootstrap and never persisted."""

    addon_urls: tuple[str, ...] = ()
    tmdb_key: str | None = None
    mdblist_key: str | None = None
    subtitle_style: SubtitleStyle = SubtitleStyle()
    track_languages: TrackLanguages = TrackLanguages()
    # Only the granted session persists. The API app credentials are
    # build-level (infrastructure/trakt/app_credentials.py), not user state.
    trakt_auth: TraktAuth | None = None
    # Whether local forgets / mark-as-watched mirror into the Trakt account.
    # Off never blocks the local action — Trakt just isn't told.
    trakt_sync_forgets: bool = True
    trakt_sync_watched: bool = True
    # False only on a fresh install that has not finished the first-run
    # wizard. The store treats a pre-onboarding settings file as done, so
    # upgrades never re-run it.
    onboarding_done: bool = False
    # Width of the picture-in-picture tile, in logical pixels; its height
    # follows the video's aspect. Position is deliberately NOT persisted: a
    # Wayland client cannot place its own window, so a remembered x/y would
    # be honoured on some platforms and silently ignored on others.
    pip_width: int = 480
    # Opt-in: order the Sources list by what this connection can actually
    # sustain, instead of the addon's own order. Off means the list is left
    # exactly as the addons returned it.
    sort_by_connection: bool = False
    # Observed download rates, newest last, capped per bucket.
    connection_samples: tuple[ConnectionSample, ...] = ()
    # Opt-in: keep sources this machine cannot display correctly out of the
    # list (today: Dolby Vision profile 5). Off means they are all shown.
    hide_incompatible: bool = False
    # Label signatures mpv has reported as an unrenderable profile. Evidence,
    # not a guess -- see application/compatibility.py.
    incompatible_sources: tuple[str, ...] = ()
    # On by default: mark the best source per quality level at the top of the
    # list. Unlike the sort it adds no order the user did not ask for when it
    # has nothing to say -- with no evidence, nothing is marked.
    recommend_sources: bool = True
    # Codec/height buckets this machine has been caught dropping frames in.
    # Evidence from mpv's own counter -- see application/playback_capability.py.
    decode_strain: tuple[str, ...] = ()
    # Pull a stream over several connections through the local proxy instead
    # of the one connection mpv would open. Off falls straight back to mpv's
    # own reader, which is what every other part of playback assumes. On by
    # default since it measured ~2s faster to the first frame (files opened in
    # ~1.6s against 3.3-4.0s straight from mpv: opening an MKV is several
    # sequential range requests, each a fresh TLS handshake without the
    # proxy's pooled connections and chunk cache). What it can cost -- a host
    # the proxy cannot reach costs a probe before the redirect hands playback
    # back to mpv -- is bounded and recovers by itself.
    parallel_streaming: bool = True
    # Ask a crowd-sourced database (SkipDB) for intro/credits times when the
    # file's own chapters do not name them. On by default like the other
    # things that help without asking; each lookup tells SkipDB what episode
    # is playing, which is what the toggle is for.
    online_segments: bool = True
    # Which engine plays video: Gravitas' own (native/player/) or mpv. Read
    # once at start-up, where it decides the video item and the graphics
    # device; GRAVITAS_PLAYER overrides it, and without the native module
    # built playback falls back to mpv whatever this says.
    video_player: VideoPlayer = "native"


@dataclass(frozen=True, slots=True)
class WatchlistEntry:
    """One saved title. `name`, `poster` and `year` are denormalized onto the
    entry (like PlaybackProgress) so the watchlist renders without refetching
    any addon's meta."""

    media_id: str
    type: MediaType
    name: str
    poster: str | None
    year: str | None
    added_at: int


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


# What the video's dynamic range is, as its badge names it.
HdrFormat = Literal["", "Dolby Vision", "HDR10+", "HDR10", "HLG"]


@dataclass(frozen=True, slots=True)
class MediaFormat:
    """What the playing file is, as the player's badges show it: read from
    the file's own tracks, never from the addon's label.

    It describes the source, not what the screen shows: on an SDR display an
    HDR file is tone-mapped, and still is HDR10."""

    hdr: HdrFormat = ""
    # 0 unless `hdr` is Dolby Vision.
    dolby_vision_profile: int = 0
    # "4K", "1440p", "1080p", "720p", or "" below that.
    resolution: str = ""
    # "Dolby Atmos" or "DTS:X" on the selected audio track, else "".
    immersive_audio: str = ""
    # "5.1" or "7.1" on the selected audio track, else "".
    channels: str = ""
