"""QObject bridge: load meta + streams for a selected item; expose meta to QML."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.get_detail import GetDetail
from gravitas.application.get_ratings import GetRatings
from gravitas.application.resolve_stream import ResolveStream
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import GravitasError, NoStreams
from gravitas.domain.models import MediaType, MetaDetail, Ratings, Video
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.stream_list_model import StreamListModel


def _season_label(season: int) -> str:
    return "Specials" if season == 0 else f"Season {season}"


class DetailController(QObject):
    errorOccurred = Signal(str)
    metaChanged = Signal()
    episodesChanged = Signal()
    sourcesChanged = Signal()
    ratingsChanged = Signal()

    def __init__(
        self,
        get_detail: GetDetail,
        resolve_stream: ResolveStream,
        stream_model: StreamListModel,
        episode_model: EpisodeListModel | None = None,
        progress: WatchProgressRepository | None = None,
        get_ratings: GetRatings | None = None,
    ) -> None:
        super().__init__()
        self._get_detail = get_detail
        self._resolve_stream = resolve_stream
        self._stream_model = stream_model
        self._episode_model = episode_model
        self._progress = progress
        self._get_ratings = get_ratings
        self._meta: MetaDetail | None = None
        self._ratings: Ratings = Ratings()
        self._ratings_task: asyncio.Task[None] | None = None
        # Season 0 ("Specials") sorts last; regular seasons ascending.
        self._seasons: list[int] = []
        self._season_idx = 0
        self._selected_episode = ""
        self._sources_label = "Sources"
        self._streams_loading = False
        self._media_id = ""
        self._media_type: MediaType = "movie"
        self._episode_label = ""
        # bumped on every load(); an in-flight load whose token no longer
        # matches is stale (a newer selection started) and must not apply its
        # result — otherwise a slow earlier load clobbers a newer one. Episode
        # stream fetches share the token so leaving the page cancels them too;
        # _ep_seq additionally serialises rapid episode clicks.
        self._seq = 0
        self._ep_seq = 0

    @Property(bool, notify=metaChanged)
    def hasMeta(self) -> bool:
        return self._meta is not None

    @Property(str, notify=metaChanged)
    def title(self) -> str:
        return self._meta.name if self._meta else ""

    @Property(str, notify=metaChanged)
    def description(self) -> str:
        return (self._meta.description or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def poster(self) -> str:
        return (self._meta.poster or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def background(self) -> str:
        return (self._meta.background or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def logo(self) -> str:
        return (self._meta.logo or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def year(self) -> str:
        return (self._meta.year or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def runtime(self) -> str:
        return (self._meta.runtime or "") if self._meta else ""

    @Property(str, notify=metaChanged)
    def imdbRating(self) -> str:
        return (self._meta.imdb_rating or "") if self._meta else ""

    @Property(str, notify=ratingsChanged)
    def rottenTomatoes(self) -> str:
        return self._ratings.rotten_tomatoes or ""

    @Property(bool, notify=ratingsChanged)
    def rottenTomatoesFresh(self) -> bool:
        return bool(self._ratings.rotten_tomatoes_fresh)

    @Property(str, notify=ratingsChanged)
    def letterboxd(self) -> str:
        return self._ratings.letterboxd or ""

    def _genres(self) -> list[str]:
        return list(self._meta.genres) if self._meta else []

    def _cast(self) -> list[str]:
        return list(self._meta.cast) if self._meta else []

    def _directors(self) -> list[str]:
        return list(self._meta.directors) if self._meta else []

    def _writers(self) -> list[str]:
        return list(self._meta.writers) if self._meta else []

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def genres(self) -> list[str]:
        return self._genres()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def cast(self) -> list[str]:
        return self._cast()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def directors(self) -> list[str]:
        return self._directors()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def writers(self) -> list[str]:
        return self._writers()

    @Property(str, notify=metaChanged)
    def trailerUrl(self) -> str:
        """A playable YouTube URL for the title's trailer, or "" if it has none.

        Empty is the signal QML uses to hide the action -- addons are not
        obliged to carry trailers.
        """
        if self._meta is None or not self._meta.trailer_yt_id:
            return ""
        return f"https://www.youtube.com/watch?v={self._meta.trailer_yt_id}"

    @Property(str, notify=metaChanged)
    def mediaId(self) -> str:
        return self._media_id

    @Property(str, notify=metaChanged)
    def mediaType(self) -> str:
        return self._media_type

    # --- episodes (series only) ---

    @Property("QVariantList", notify=episodesChanged)  # type: ignore[arg-type]
    def seasonOptions(self) -> list[str]:
        return [_season_label(s) for s in self._seasons]

    @Property(int, notify=episodesChanged)
    def seasonIndex(self) -> int:
        return self._season_idx

    @Property(str, notify=sourcesChanged)
    def selectedEpisodeId(self) -> str:
        return self._selected_episode

    @Slot(result="QVariantMap")
    def mediaContext(self) -> dict[str, str]:
        """What is currently selected for playback — the movie, or the chosen
        episode. Handed to PlayerController.setMediaContext() before play()."""
        return {
            "mediaId": self._media_id,
            "videoId": self._selected_episode,
            "type": self._media_type,
            "name": self.title,  # type: ignore[dict-item]
            "poster": self.poster,  # type: ignore[dict-item]
            "label": self._episode_label,
        }

    @Property(str, notify=sourcesChanged)
    def sourcesLabel(self) -> str:
        return self._sources_label

    @Property(bool, notify=sourcesChanged)
    def streamsLoading(self) -> bool:
        return self._streams_loading

    def _season_videos(self, season: int) -> list[Video]:
        videos = self._meta.videos if self._meta else ()
        picked = [v for v in videos if (v.season or 0) == season]
        picked.sort(key=lambda v: v.episode or 0)
        return picked

    def _build_seasons(self) -> None:
        videos = self._meta.videos if self._meta else ()
        distinct = {v.season or 0 for v in videos}
        # Specials (season 0) after the regular seasons.
        self._seasons = sorted(s for s in distinct if s != 0)
        if 0 in distinct:
            self._seasons.append(0)
        self._season_idx = 0
        resume = self._resume_video()
        if resume is not None:
            # Arriving from Continue Watching, open on the season holding the
            # episode you were on — otherwise the episode we select below is
            # not even in the visible list.
            self._season_idx = self._seasons.index(resume.season or 0)
        if self._episode_model is not None:
            episodes = self._season_videos(self._seasons[self._season_idx]) if self._seasons else []
            self._episode_model.set_videos(episodes)
        self.episodesChanged.emit()
        if resume is not None:
            self._select_episode(resume.id, resume.season or 0, resume.episode or 0, resume.title)

    def _resume_video(self) -> Video | None:
        """The episode this series would resume into, if the saved one is still
        listed by the current meta. Stale progress (an episode the addon no
        longer serves) selects nothing rather than a phantom."""
        if self._progress is None or self._meta is None:
            return None
        entry = self._progress.latest_unwatched_for(self._media_id)
        if entry is None or not entry.video_id:
            return None
        return next((v for v in self._meta.videos if v.id == entry.video_id), None)

    @Slot(int)
    def selectSeason(self, index: int) -> None:
        if not 0 <= index < len(self._seasons) or self._episode_model is None:
            return
        self._season_idx = index
        self._episode_model.set_videos(self._season_videos(self._seasons[index]))
        self.episodesChanged.emit()

    def _select_episode(self, video_id: str, season: int, episode: int, title: str) -> None:
        """Mark an episode as the current selection. Synchronous and network-
        free: preselecting a resumed episode must highlight the row and set the
        media context without fetching streams the user may never ask for —
        they are fetched when the episode is actually clicked."""
        self._selected_episode = video_id
        self._episode_label = f"S{season}E{episode} · {title}" if title else f"S{season}E{episode}"
        label = f"Sources — S{season}E{episode}"
        self._sources_label = f"{label} · {title}" if title else label
        self.sourcesChanged.emit()

    @asyncSlot(str, int, int, str)  # type: ignore[untyped-decorator]
    async def selectEpisode(self, video_id: str, season: int, episode: int, title: str) -> None:
        load_token = self._seq
        self._ep_seq += 1
        ep_token = self._ep_seq
        self._select_episode(video_id, season, episode, title)
        self._streams_loading = True
        self.sourcesChanged.emit()
        self._stream_model.set_streams([])
        try:
            streams = await self._resolve_stream("series", video_id)
            if load_token != self._seq or ep_token != self._ep_seq:
                return  # left the page or picked another episode meanwhile
            self._stream_model.set_streams(streams)
        except NoStreams:
            pass  # empty Sources is the honest state, not an error toast
        except GravitasError as exc:
            if load_token == self._seq and ep_token == self._ep_seq:
                self.errorOccurred.emit(str(exc))
        finally:
            if load_token == self._seq and ep_token == self._ep_seq:
                self._streams_loading = False
                self.sourcesChanged.emit()

    @asyncSlot(str, str)  # type: ignore[untyped-decorator]
    async def load(self, type: str, item_id: str) -> None:
        media_type: MediaType = "series" if type == "series" else "movie"
        self._seq += 1
        token = self._seq
        # reset immediately so the new page never flashes the previous item's
        # meta/streams while this load is in flight
        self._meta = None
        self.metaChanged.emit()
        self._ratings = Ratings()
        self.ratingsChanged.emit()
        self._stream_model.set_streams([])
        self._seasons = []
        self._season_idx = 0
        self._selected_episode = ""
        self._sources_label = "Sources"
        self._streams_loading = False
        self._media_id = item_id
        self._media_type = media_type
        self._episode_label = ""
        if self._episode_model is not None:
            self._episode_model.set_media_id(item_id)
            self._episode_model.set_videos([])
        self.episodesChanged.emit()
        self.sourcesChanged.emit()
        try:
            meta = await self._get_detail(media_type, item_id)
            if token != self._seq:
                return  # a newer load started; drop this stale result
            self._meta = meta
            self.metaChanged.emit()
            if self._get_ratings is not None and meta.id.startswith("tt"):
                # Fire-and-forget: ratings must not delay the stream fetch below,
                # and GetRatings already swallows failures into empty Ratings.
                # Kept on self so the task isn't GC'd mid-flight (RUF006); it is
                # never awaited here on purpose.
                self._ratings_task = asyncio.ensure_future(
                    self._load_ratings(token, meta.id, media_type)
                )
            if media_type == "series" and meta.videos and self._episode_model is not None:
                # Episode-driven flow: no stream fetch until the user picks an
                # episode (streams are per-episode video ids).
                self._build_seasons()
                return
            self._streams_loading = True
            self.sourcesChanged.emit()
            # behaviorHints.defaultVideoId names the video whose streams stand
            # for the title itself. Cinemeta echoes the meta id, but an addon
            # whose stream id differs would otherwise be asked for the wrong
            # one and answer with nothing.
            stream_id = meta.default_video_id or item_id
            streams = await self._resolve_stream(media_type, stream_id)
            if token != self._seq:
                return
            self._stream_model.set_streams(streams)
        except NoStreams:
            # a normal empty state (no stream addon configured, or none for
            # this title) — leave Sources empty, don't raise a red error toast
            pass
        except GravitasError as exc:
            if token == self._seq:
                self.errorOccurred.emit(str(exc))
        finally:
            if token == self._seq and self._streams_loading:
                self._streams_loading = False
                self.sourcesChanged.emit()

    async def _load_ratings(self, token: int, imdb_id: str, media_type: MediaType) -> None:
        get_ratings = self._get_ratings
        if get_ratings is None:
            return
        ratings = await get_ratings(imdb_id, media_type)
        if token != self._seq:
            return  # a newer load started; drop this stale result
        self._ratings = ratings
        self.ratingsChanged.emit()
