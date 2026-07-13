"""QObject bridge: load meta + streams for a selected item; expose meta to QML."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.get_detail import GetDetail
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import GravitasError, NoStreams
from gravitas.domain.models import MediaType, MetaDetail, Video
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.stream_list_model import StreamListModel


def _season_label(season: int) -> str:
    return "Specials" if season == 0 else f"Season {season}"


class DetailController(QObject):
    errorOccurred = Signal(str)
    metaChanged = Signal()
    episodesChanged = Signal()
    sourcesChanged = Signal()

    def __init__(
        self,
        get_detail: GetDetail,
        resolve_stream: ResolveStream,
        stream_model: StreamListModel,
        episode_model: EpisodeListModel | None = None,
    ) -> None:
        super().__init__()
        self._get_detail = get_detail
        self._resolve_stream = resolve_stream
        self._stream_model = stream_model
        self._episode_model = episode_model
        self._meta: MetaDetail | None = None
        # Season 0 ("Specials") sorts last; regular seasons ascending.
        self._seasons: list[int] = []
        self._season_idx = 0
        self._selected_episode = ""
        self._sources_label = "Sources"
        self._streams_loading = False
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

    def _genres(self) -> list[str]:
        return list(self._meta.genres) if self._meta else []

    def _cast(self) -> list[str]:
        return list(self._meta.cast) if self._meta else []

    def _directors(self) -> list[str]:
        return list(self._meta.directors) if self._meta else []

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def genres(self) -> list[str]:
        return self._genres()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def cast(self) -> list[str]:
        return self._cast()

    @Property("QVariantList", notify=metaChanged)  # type: ignore[arg-type]
    def directors(self) -> list[str]:
        return self._directors()

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
        if self._episode_model is not None:
            episodes = self._season_videos(self._seasons[0]) if self._seasons else []
            self._episode_model.set_videos(episodes)
        self.episodesChanged.emit()

    @Slot(int)
    def selectSeason(self, index: int) -> None:
        if not 0 <= index < len(self._seasons) or self._episode_model is None:
            return
        self._season_idx = index
        self._episode_model.set_videos(self._season_videos(self._seasons[index]))
        self.episodesChanged.emit()

    @asyncSlot(str, int, int, str)  # type: ignore[untyped-decorator]
    async def selectEpisode(self, video_id: str, season: int, episode: int, title: str) -> None:
        load_token = self._seq
        self._ep_seq += 1
        ep_token = self._ep_seq
        self._selected_episode = video_id
        label = f"Sources — S{season}E{episode}"
        self._sources_label = f"{label} · {title}" if title else label
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
        self._stream_model.set_streams([])
        self._seasons = []
        self._season_idx = 0
        self._selected_episode = ""
        self._sources_label = "Sources"
        self._streams_loading = False
        if self._episode_model is not None:
            self._episode_model.set_videos([])
        self.episodesChanged.emit()
        self.sourcesChanged.emit()
        try:
            meta = await self._get_detail(media_type, item_id)
            if token != self._seq:
                return  # a newer load started; drop this stale result
            self._meta = meta
            self.metaChanged.emit()
            if media_type == "series" and meta.videos and self._episode_model is not None:
                # Episode-driven flow: no stream fetch until the user picks an
                # episode (streams are per-episode video ids).
                self._build_seasons()
                return
            streams = await self._resolve_stream(media_type, item_id)
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
