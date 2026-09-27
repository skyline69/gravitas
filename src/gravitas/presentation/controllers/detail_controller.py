"""QObject bridge: load meta + streams for a selected item; expose meta to QML."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Property, QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.compatibility import IncompatibleSources, partition
from gravitas.application.connection_speed import ConnectionSpeed
from gravitas.application.episode_search import find_episodes
from gravitas.application.get_detail import GetDetail
from gravitas.application.get_ratings import GetRatings
from gravitas.application.playback_capability import PlaybackCapability
from gravitas.application.recommendation import SourceRecommendations, recommend
from gravitas.application.resolve_stream import ResolveStream
from gravitas.application.stream_ranking import (
    is_oversized,
    parse_runtime_seconds,
    rank_streams,
)
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import GravitasError, NoStreams, SourcesUnavailable
from gravitas.domain.models import MediaType, MetaDetail, Ratings, Stream, Video
from gravitas.domain.ports import BandwidthProbe
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.stream_list_model import StreamListModel


@dataclass(frozen=True, slots=True)
class _RenderInputs:
    """Everything the ranking reads, snapshotted on the GUI thread so the
    ranking itself can run on a worker."""

    shown: tuple[Stream, ...]
    # The hidden rows the user asked to see; () while they are held back.
    revealed: tuple[Stream, ...]
    sort_on: bool
    kbps: int | None
    height: int
    runtime_s: float | None
    # None when recommendations are off.
    recommend_limit: int | None
    known_bad: frozenset[str]
    strained: frozenset[str]


def _rank_for_display(
    inputs: _RenderInputs,
) -> tuple[list[Stream], tuple[bool, ...], tuple[bool, ...], tuple[str, ...]]:
    """The rows in display order, with their over-budget, oversized and
    recommendation flags. Pure, so it runs off the GUI thread."""
    sort_height = inputs.height if inputs.sort_on else 0
    ranked, over_budget = rank_streams(
        list(inputs.shown),
        inputs.kbps if inputs.sort_on else None,
        runtime_s=inputs.runtime_s,
        display_height=sort_height,
    )
    oversized = tuple(is_oversized(s, sort_height) for s in ranked)
    # A chip on every single row is not information. On a laptop panel every
    # 4K source is downscaled, so "Downscaled" everywhere says only that the
    # screen is small -- which the Settings page already states, once. The
    # mark is kept only while it distinguishes rows from each other; when it
    # is unanimous it also changes no ordering, so dropping it costs nothing.
    if all(oversized):
        oversized = tuple(False for _ in ranked)
    ranked, over_budget, oversized, reasons = _mark_recommended(
        ranked, over_budget, oversized, inputs
    )
    if inputs.revealed:
        # Revealed rows go last, unranked and unmarked: they are shown because
        # the user asked, not because anything about them changed.
        ranked = [*ranked, *inputs.revealed]
        over_budget = (*over_budget, *(False for _ in inputs.revealed))
        oversized = (*oversized, *(False for _ in inputs.revealed))
        reasons = (*reasons, *("" for _ in inputs.revealed))
    return ranked, over_budget, oversized, reasons


def _mark_recommended(
    ranked: list[Stream],
    over_budget: tuple[bool, ...],
    oversized: tuple[bool, ...],
    inputs: _RenderInputs,
) -> tuple[list[Stream], tuple[bool, ...], tuple[bool, ...], tuple[str, ...]]:
    """Mark the best row per quality level and lift the marked rows to the
    top, carrying every other per-row flag with them.

    The lift is what makes the mark worth having: a recommendation ten rows
    down is a chip, not an answer. Everything below keeps the order
    rank_streams gave it, so the rest of the list is unchanged apart from the
    handful of rows that left it.
    """
    blank = tuple("" for _ in ranked)
    if inputs.recommend_limit is None or not ranked:
        return ranked, over_budget, oversized, blank
    picks = recommend(
        ranked,
        kbps=inputs.kbps,
        display_height=inputs.height,
        runtime_s=inputs.runtime_s,
        known_bad=inputs.known_bad,
        strained=inputs.strained,
        limit=inputs.recommend_limit,
    )
    if not picks:
        return ranked, over_budget, oversized, blank
    reasons = [""] * len(ranked)
    for pick in picks:
        reasons[pick.index] = pick.reason
    marked = [pick.index for pick in picks]
    rest = set(marked)
    order = [*marked, *(i for i in range(len(ranked)) if i not in rest)]
    return (
        [ranked[i] for i in order],
        tuple(over_budget[i] for i in order),
        tuple(oversized[i] for i in order),
        tuple(reasons[i] for i in order),
    )


def _season_label(season: int) -> str:
    return "Specials" if season == 0 else f"Season {season}"


class DetailController(QObject):
    errorOccurred = Signal(str)
    metaChanged = Signal()
    episodesChanged = Signal()
    sourcesChanged = Signal()
    # playEpisode's answers, by video id: its sources are in the list and row 0
    # is the one to play, or there is nothing to play and why.
    episodeReadyToPlay = Signal(str)
    episodeUnplayable = Signal(str, str)
    # pickWhenFresh's answer: the picked release's row in the list that
    # replaced the stored one, or -1 when it is not in it (or the page moved
    # on).
    freshSourceReady = Signal(int)
    ratingsChanged = Signal()
    previewChanged = Signal()

    def __init__(
        self,
        get_detail: GetDetail,
        resolve_stream: ResolveStream,
        stream_model: StreamListModel,
        episode_model: EpisodeListModel | None = None,
        progress: WatchProgressRepository | None = None,
        get_ratings: GetRatings | None = None,
        connection: ConnectionSpeed | None = None,
        probe: BandwidthProbe | None = None,
        on_samples_changed: Callable[[], None] | None = None,
        display_height: Callable[[], int] | None = None,
        incompatible: IncompatibleSources | None = None,
        recommendations: SourceRecommendations | None = None,
        capability: PlaybackCapability | None = None,
        prefetch: Callable[[MediaType, str], None] | None = None,
        stored: Callable[[MediaType, str], Awaitable[list[Stream]]] | None = None,
        speculate: Callable[[MediaType, str], None] | None = None,
        stored_meta: Callable[[MediaType, str], Awaitable[MetaDetail | None]] | None = None,
        warm_link: Callable[[str, Sequence[tuple[str, str]]], None] | None = None,
        episode_search_model: EpisodeListModel | None = None,
    ) -> None:
        super().__init__()
        self._get_detail = get_detail
        self._resolve_stream = resolve_stream
        self._stream_model = stream_model
        self._episode_model = episode_model
        # The episodes the search box names, from every season. Its own model
        # rather than a filter on EpisodeModel, which the player's episode
        # panel shows too.
        self._episode_search_model = episode_search_model
        self._episode_query = ""
        self._progress = progress
        self._get_ratings = get_ratings
        self._connection = connection
        self._probe = probe
        self._on_samples_changed = on_samples_changed
        # Read per sort, not cached: a screen can be plugged in, unplugged or
        # rearranged between two Sources pages, and the query is cheap.
        self._display_height = display_height
        self._incompatible = incompatible
        # The marks at the top of the list, and the decode evidence behind
        # them. Both read-only here: the player writes what it learns.
        self._recommendations = recommendations
        self._capability = capability
        # Starts a stream fetch ahead of the click that will need it
        # (ResolveStream.prefetch). The addon's own answer takes seconds, and
        # this is the only lever on that wait the client has: begin it sooner.
        self._prefetch = prefetch
        # The last list the addons gave for a video (ResolveStream.stored),
        # shown while the fresh request runs so a revisit has rows at once.
        self._stored = stored
        # prefetch() on weaker evidence (ResolveStream.speculate): a pointer
        # resting on a poster, which the resolver may decline.
        self._speculate = speculate
        # The last meta kept on disk for a title (GetDetail.stored), shown when
        # the fresh fetch is slow -- see _fetch_meta.
        self._stored_meta = stored_meta
        # Resolves the top row's redirect while the list is read
        # (LinkWarmup.warm), so the click starts on the CDN link.
        self._warm_link = warm_link
        # Background work started by prefetchTitle, held so the loop's weak
        # task references cannot drop it mid-flight.
        self._warming: set[asyncio.Task[None]] = set()
        # What the clicked card already showed -- its title and poster -- for
        # the page to stand on while a slow meta arrives. Keyed by id like the
        # meta, and never cleared by load(): it only ever describes the title
        # it names.
        self._preview_for = ""
        self._preview_name = ""
        self._preview_poster = ""
        # The addon failed to answer and nothing was shown (SourcesUnavailable):
        # the page offers a retry instead of claiming there is nothing to play.
        self._sources_failed = False
        # What retrySources() asks for again: the title's stream id, or the
        # episode selection (video id, season, episode, title).
        self._title_stream_id = ""
        self._episode_args: tuple[str, int, int, str] | None = None
        # What the current list is holding back, and whether the user has
        # asked to see it anyway. Both reset with every new list -- "show the
        # hidden ones" is about the page in front of the user, not a setting.
        self._shown_streams: list[Stream] = []
        self._hidden_streams: list[Stream] = []
        self._reveal_hidden = False
        self._meta: MetaDetail | None = None
        self._ratings: Ratings = Ratings()
        # Which media id `_ratings` describes. load() is an asyncSlot, so its
        # reset runs a loop turn AFTER QML calls it -- long enough for a freshly
        # built Detail page to bind against the PREVIOUS item's ratings. The
        # page compares this against its own id and shows nothing until they
        # agree, so a stale pill can never be on screen.
        self._ratings_id: str = ""
        self._ratings_task: asyncio.Task[None] | None = None
        # The same guard for the meta itself: the id `_meta` was loaded for,
        # and the id whose meta could not be loaded at all. A freshly pushed
        # Detail page compares both against its own id, so it never shows the
        # previous title for the loop turn before load() resets, and can tell
        # "still loading" (throbber) from "failed" (retry) without a timer.
        self._meta_for = ""
        self._meta_failed_for = ""
        # Season 0 ("Specials") sorts last; regular seasons ascending.
        self._seasons: list[int] = []
        self._season_idx = 0
        self._selected_episode = ""
        self._sources_label = "Sources"
        self._streams_loading = False
        # The rows on screen came from disk (see _show_stored), and their
        # links may be from another session: signed for an address this
        # machine no longer has, or expired.
        self._rows_stored = False
        # A stored row clicked before the fresh list arrived (pickWhenFresh).
        self._pending_pick: tuple[str, str, int] | None = None
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
        # Bumped by every render and every reset; a ranking that comes back
        # from its thread under an older number has been overtaken.
        self._render_seq = 0

    @Property(bool, notify=metaChanged)
    def hasMeta(self) -> bool:
        return self._meta is not None

    @Property(str, notify=previewChanged)
    def previewFor(self) -> str:
        return self._preview_for

    @Property(str, notify=previewChanged)
    def previewName(self) -> str:
        return self._preview_name

    @Property(str, notify=previewChanged)
    def previewPoster(self) -> str:
        return self._preview_poster

    @Slot(str, str, str)
    def preview(self, item_id: str, name: str, poster: str) -> None:
        """Record what the clicked card showed. QML calls this on the click,
        before the Detail page is pushed."""
        self._preview_for = item_id
        self._preview_name = name
        self._preview_poster = poster
        self.previewChanged.emit()

    @Slot(str, str)
    def prefetchTitle(self, type: str, item_id: str) -> None:
        """Start on a title before it is opened -- QML calls this when the
        pointer settles on a poster. Warms the meta (the click then joins the
        request, see GetDetail) and guesses at the sources the page will want:
        the film's own, or the episode a series would open on."""
        if not item_id:
            return
        media_type: MediaType = "series" if type == "series" else "movie"
        if media_type == "movie" and self._speculate is not None:
            self._speculate(media_type, item_id)
        task = asyncio.ensure_future(self._warm(media_type, item_id))
        self._warming.add(task)
        task.add_done_callback(self._warming.discard)

    async def _warm(self, media_type: MediaType, item_id: str) -> None:
        try:
            meta = await self._get_detail(media_type, item_id)
        except GravitasError:
            return  # a guess that failed; the click will ask, and report, itself
        if media_type != "series" or self._speculate is None:
            return
        likely = self._likely_video(meta, item_id)
        if likely is not None:
            self._speculate("series", likely.id)

    def prefetch_after(self, media_id: str, video_id: str) -> None:
        """Start the episode after `video_id`, because the one playing is about
        to end (the player calls this). Only for the series this page holds:
        the episode list is the meta's, and the viewer goes back through it."""
        if self._prefetch is None or self._meta is None or self._media_id != media_id:
            return
        following = _following_video(self._meta.videos, video_id)
        if following is not None:
            self._prefetch("series", following.id)

    @Property(bool, notify=sourcesChanged)
    def sourcesFailed(self) -> bool:
        return self._sources_failed

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def retrySources(self) -> None:
        """Ask for the current title's or episode's sources again."""
        if self._episode_args is not None and self._selected_episode:
            await self._episode_streams(*self._episode_args)
        elif self._title_stream_id:
            await self._title_streams(self._media_type, self._title_stream_id, self._seq)

    @Property(str, notify=metaChanged)
    def metaFor(self) -> str:
        return self._meta_for

    @Property(str, notify=metaChanged)
    def metaFailedFor(self) -> str:
        return self._meta_failed_for

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

    @Property(str, notify=ratingsChanged)
    def ratingsFor(self) -> str:
        return self._ratings_id

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

    @Property(bool, notify=episodesChanged)
    def hasEpisodes(self) -> bool:
        """The title has an episode list, so its sources are per episode and
        not listed on the title page itself."""
        return bool(self._seasons)

    @Property("QVariantList", notify=episodesChanged)  # type: ignore[arg-type]
    def seasonOptions(self) -> list[str]:
        return [_season_label(s) for s in self._seasons]

    @Property(int, notify=episodesChanged)
    def seasonIndex(self) -> int:
        return self._season_idx

    @Property(str, notify=episodesChanged)
    def episodeQuery(self) -> str:
        """What the episode search box holds; empty lists the season."""
        return self._episode_query

    @Slot(str)
    def setEpisodeQuery(self, query: str) -> None:
        if query == self._episode_query:
            return
        self._episode_query = query
        self._apply_episode_query()
        self.episodesChanged.emit()

    def _apply_episode_query(self) -> None:
        if self._episode_search_model is None:
            return
        videos = self._meta.videos if self._meta else ()
        self._episode_search_model.set_media_id(self._media_id)
        self._episode_search_model.set_videos(find_episodes(videos, self._episode_query))

    @Property(str, notify=sourcesChanged)
    def selectedEpisodeId(self) -> str:
        return self._selected_episode

    @Slot(int, result="QVariantList")
    def sourceQueue(self, row: int) -> list[dict[str, object]]:
        """The sources listed after `row`, best first, for automatic failover.

        A debrid CDN node that refuses the connection takes every source
        pointing at it with it, and that says nothing about the title. The
        player walks this list rather than showing an error and asking the
        viewer to click the next row themselves.
        """
        model = self._stream_model
        queue: list[dict[str, object]] = []
        for index in range(row + 1, model.rowCount()):
            stream = model.stream_at(index)
            url = stream.playable_url
            if not url:
                continue
            queue.append(
                {
                    "url": url,
                    "headers": dict(stream.proxy_headers),
                    "name": stream.name,
                    "title": stream.title,
                    "filename": stream.filename,
                }
            )
        return queue

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

    @Property(bool, notify=sourcesChanged)
    def sourcesStored(self) -> bool:
        """The rows on screen are the list kept from an earlier visit, and a
        fresh one is on its way. Their links must not be played: a signed
        link can be bound to the address that asked for it (measured, an
        ElfHosted link after the ISP changed the /64 served a "Wrong IP"
        video instead of the episode), and it can have expired. A click
        meanwhile goes through pickWhenFresh."""
        return self._rows_stored and self._streams_loading

    @Slot(int)
    def pickWhenFresh(self, row: int) -> None:
        """Play `row`'s release once the fresh list is in: freshSourceReady
        then names its row there. A later pick replaces an earlier one."""
        if not 0 <= row < self._stream_model.rowCount():
            return
        self._pending_pick = self._stream_model.key_at(row)
        if not self.sourcesStored:
            self._settle_pick()

    def _settle_pick(self) -> None:
        """Answer the pending pick from the list on screen now: the fresh
        one, or -- when the fresh request failed or came back empty -- the
        stored one it left standing, which is better than nothing."""
        key, self._pending_pick = self._pending_pick, None
        if key is not None:
            self.freshSourceReady.emit(self._stream_model.row_of(key))

    @Slot(int, result="QVariantMap")
    def sourceAt(self, row: int) -> dict[str, object]:
        """`row` as the page plays it: its link, headers and label."""
        if not 0 <= row < self._stream_model.rowCount():
            return {}
        stream = self._stream_model.stream_at(row)
        return {
            "url": stream.playable_url or "",
            "headers": dict(stream.proxy_headers),
            "name": stream.name,
            "title": stream.title,
            "filename": stream.filename or "",
        }

    @Property(int, notify=sourcesChanged)
    def hiddenSourceCount(self) -> int:
        """How many sources this machine cannot display correctly are being
        held back from the current list. 0 once they are revealed -- the count
        is an offer, and an accepted offer is not still pending."""
        return 0 if self._reveal_hidden else len(self._hidden_streams)

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def showHiddenSources(self) -> None:
        """Show them anyway. The estimate of what is unplayable can be wrong,
        and a user who wants to judge for themselves is entitled to."""
        if not self._hidden_streams or self._reveal_hidden:
            return
        self._reveal_hidden = True
        self.sourcesChanged.emit()
        await self._render_streams()

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
        # The episode this page is most likely to be opened on: the one it
        # resumes into, else the first of the season on screen. One request
        # per series opened, spent where the next click probably lands.
        likely = self._likely_video(self._meta, self._media_id) if self._meta else None
        # Not a guess from a passing pointer: the page opens on this one.
        if likely is not None and self._prefetch is not None:
            self._prefetch("series", likely.id)

    def _resume_video(self) -> Video | None:
        """The episode this series would resume into, if the saved one is still
        listed by the current meta. Stale progress (an episode the addon no
        longer serves) selects nothing rather than a phantom."""
        if self._meta is None:
            return None
        return self._resume_video_in(self._meta, self._media_id)

    def _resume_video_in(self, meta: MetaDetail, media_id: str) -> Video | None:
        if self._progress is None:
            return None
        entry = self._progress.latest_unwatched_for(media_id)
        if entry is None or not entry.video_id:
            return None
        return next((v for v in meta.videos if v.id == entry.video_id), None)

    def _likely_video(self, meta: MetaDetail, media_id: str) -> Video | None:
        """The episode a series page is most likely to be opened on: the one it
        resumes into, else the first of the season it opens on (the first
        regular season; Specials only when there is nothing else)."""
        resume = self._resume_video_in(meta, media_id)
        if resume is not None:
            return resume
        seasons = {v.season or 0 for v in meta.videos}
        regular = sorted(s for s in seasons if s != 0)
        first = regular[0] if regular else 0
        episodes = sorted(
            (v for v in meta.videos if (v.season or 0) == first), key=lambda v: v.episode or 0
        )
        return episodes[0] if episodes else None

    @Slot(int)
    def selectSeason(self, index: int) -> None:
        if not 0 <= index < len(self._seasons) or self._episode_model is None:
            return
        self._season_idx = index
        self._episode_model.set_videos(self._season_videos(self._seasons[index]))
        # Picking a season is asking to see it, not the matches.
        self._episode_query = ""
        self._apply_episode_query()
        self.episodesChanged.emit()

    def _select_episode(self, video_id: str, season: int, episode: int, title: str) -> None:
        """Mark an episode as the current selection. Synchronous and network-
        free: preselecting a resumed episode must highlight the row and set the
        media context without fetching streams the user may never ask for —
        they are fetched when the episode is actually clicked."""
        self._selected_episode = video_id
        self._episode_label = f"S{season}E{episode} · {title}" if title else f"S{season}E{episode}"
        label = f"Sources: S{season}E{episode}"
        self._sources_label = f"{label} · {title}" if title else label
        self.sourcesChanged.emit()

    @Slot(str)
    def prefetchEpisode(self, video_id: str) -> None:
        """Guess at an episode's sources before it is clicked -- QML calls this
        when the pointer settles on an episode row.

        A guess, so it goes through the capped path (ResolveStream.speculate)
        like a poster does. It used the uncapped prefetch, and running the
        pointer down a season fired one aggregator scrape per row it rested
        on: five for one show inside two seconds in a real session log, each
        a debrid-checked search the account is rate-limited on."""
        if self._speculate is not None and video_id:
            self._speculate("series", video_id)

    @asyncSlot(str, int, int, str)  # type: ignore[untyped-decorator]
    async def selectEpisode(self, video_id: str, season: int, episode: int, title: str) -> None:
        await self._episode_streams(video_id, season, episode, title)

    @asyncSlot(str, int, int, str)  # type: ignore[untyped-decorator]
    async def playEpisode(self, video_id: str, season: int, episode: int, title: str) -> None:
        """The player's episode panel: fetch an episode's sources exactly as
        the Sources page would -- stored list, fresh fetch, ranking, the top
        link warmed -- and say when row 0 is ready to play. The player then
        plays it with the rest of the list as its fallback queue, the same as
        a click on the Sources page's first row.

        Answers only for the latest request: clicking a second episode while
        the first is still resolving must play the second, never whichever
        answered last.
        """
        token = self._ep_seq + 1
        await self._episode_streams(video_id, season, episode, title)
        if token != self._ep_seq or self._selected_episode != video_id:
            return
        if self.sourceQueue(-1):
            self.episodeReadyToPlay.emit(video_id)
        elif self._sources_failed:
            self.episodeUnplayable.emit(video_id, "Sources didn't answer. Try again in a moment.")
        else:
            self.episodeUnplayable.emit(video_id, "No playable sources for this episode.")

    @Slot(str, result="QVariantMap")
    def episodeInfo(self, video_id: str) -> dict[str, object]:
        """One episode of the loaded series, for the player panel's header:
        title, overview, thumbnail, season, episode and the "S1E6 · Title"
        label. Empty when the id is not one of this series' episodes."""
        videos = self._meta.videos if self._meta else ()
        video = next((v for v in videos if v.id == video_id), None)
        if video is None:
            return {}
        season, episode = video.season or 0, video.episode or 0
        label = f"S{season}E{episode}" + (f" · {video.title}" if video.title else "")
        return {
            "title": video.title,
            "overview": video.overview or "",
            "thumbnail": video.thumbnail or "",
            "season": season,
            "episode": episode,
            "label": label,
        }

    @Slot(str, result="QVariantMap")
    def nextEpisode(self, video_id: str) -> dict[str, object]:
        """The episode after `video_id` in watching order, for the player's
        next-episode button, shaped like episodeInfo plus its videoId and
        whether it opens a new season. The next episode of the season, else
        the first of the next one (a gap in the numbering is skipped, not
        waited for); specials only follow specials. Empty after the last
        episode there is."""
        videos = self._meta.videos if self._meta else ()
        current = next((v for v in videos if v.id == video_id), None)
        following = _following_video(videos, video_id) if current is not None else None
        if following is None or current is None:
            return {}
        info = self.episodeInfo(following.id)
        info["videoId"] = following.id
        info["newSeason"] = (following.season or 0) != (current.season or 0)
        return info

    @Slot(str, result=int)
    def episodeRowOf(self, video_id: str) -> int:
        """`video_id`'s row in the season now listed, or -1 -- so the panel
        can scroll the playing episode into view."""
        if not self._seasons:
            return -1
        videos = self._season_videos(self._seasons[self._season_idx])
        return next((row for row, v in enumerate(videos) if v.id == video_id), -1)

    @Slot(str, result=int)
    def seasonIndexOf(self, video_id: str) -> int:
        """Where the season holding `video_id` sits in seasonOptions, or -1.
        The panel opens on the playing episode's season, whatever season the
        Detail page was left showing."""
        videos = self._meta.videos if self._meta else ()
        video = next((v for v in videos if v.id == video_id), None)
        if video is None or (video.season or 0) not in self._seasons:
            return -1
        return self._seasons.index(video.season or 0)

    async def _episode_streams(self, video_id: str, season: int, episode: int, title: str) -> None:
        load_token = self._seq
        self._ep_seq += 1
        ep_token = self._ep_seq
        self._episode_args = (video_id, season, episode, title)
        self._select_episode(video_id, season, episode, title)
        self._streams_loading = True
        self._sources_failed = False
        self._reveal_hidden = False
        self._shown_streams = []
        self._hidden_streams = []
        self.sourcesChanged.emit()
        self._clear_streams()
        try:
            await self._show_stored("series", video_id, load_token, ep_token)
            streams = await self._resolve_stream("series", video_id)
            if load_token != self._seq or ep_token != self._ep_seq:
                return  # left the page or picked another episode meanwhile
            await self._apply_streams(streams, load_token, ep_token)
        except SourcesUnavailable:
            if load_token == self._seq and ep_token == self._ep_seq:
                self._sources_failed = True
        except NoStreams:
            pass  # empty Sources is the honest state, not an error toast
        except GravitasError as exc:
            if load_token == self._seq and ep_token == self._ep_seq:
                self.errorOccurred.emit(str(exc))
        finally:
            if load_token == self._seq and ep_token == self._ep_seq:
                self._streams_loading = False
                self.sourcesChanged.emit()
                self._settle_pick()

    @asyncSlot(str, str)  # type: ignore[untyped-decorator]
    async def load(self, type: str, item_id: str) -> None:
        media_type: MediaType = "series" if type == "series" else "movie"
        self._seq += 1
        token = self._seq
        # reset immediately so the new page never flashes the previous item's
        # meta/streams while this load is in flight
        self._meta = None
        self._meta_for = ""
        self._meta_failed_for = ""
        self.metaChanged.emit()
        self._ratings = Ratings()
        self._ratings_id = ""
        self.ratingsChanged.emit()
        self._clear_streams()
        self._seasons = []
        self._season_idx = 0
        self._selected_episode = ""
        self._sources_label = "Sources"
        # Loading from the first frame, not from when the stream fetch starts:
        # the meta comes first, and a page reading "not loading, no sources"
        # meanwhile says "No sources available" for as long as that takes.
        self._streams_loading = True
        self._sources_failed = False
        self._title_stream_id = ""
        self._episode_args = None
        self._reveal_hidden = False
        self._shown_streams = []
        self._hidden_streams = []
        self._media_id = item_id
        self._media_type = media_type
        self._episode_label = ""
        if self._episode_model is not None:
            self._episode_model.set_media_id(item_id)
            self._episode_model.set_videos([])
        self._episode_query = ""
        self._apply_episode_query()
        self.episodesChanged.emit()
        self.sourcesChanged.emit()
        if media_type == "movie" and self._prefetch is not None:
            # Alongside the meta rather than after it: the meta is usually a
            # cache hit and the streams never are. Only a defaultVideoId that
            # differs from the id wastes this, and that is rare.
            self._prefetch(media_type, item_id)
        try:
            meta = await self._fetch_meta(media_type, item_id, token)
            if token != self._seq:
                return  # a newer load started; drop this stale result
            episodic = self._show_meta(meta, item_id, media_type)
            if self._get_ratings is not None and meta.id.startswith("tt"):
                # Fire-and-forget: ratings must not delay the stream fetch below,
                # and GetRatings already swallows failures into empty Ratings.
                # Kept on self so the task isn't GC'd mid-flight (RUF006); it is
                # never awaited here on purpose.
                self._ratings_task = asyncio.ensure_future(
                    self._load_ratings(token, meta.id, media_type)
                )
            if episodic:
                return
            # behaviorHints.defaultVideoId names the video whose streams stand
            # for the title itself. Cinemeta echoes the meta id, but an addon
            # whose stream id differs would otherwise be asked for the wrong
            # one and answer with nothing.
            self._title_stream_id = meta.default_video_id or item_id
            await self._title_streams(media_type, self._title_stream_id, token)
        except GravitasError as exc:
            if token == self._seq:
                if self._meta is None:
                    self._meta_failed_for = item_id
                    self.metaChanged.emit()
                self.errorOccurred.emit(str(exc))
        finally:
            if token == self._seq and self._streams_loading:
                self._streams_loading = False
                self.sourcesChanged.emit()

    # How long the fresh meta fetch gets before the page shows the copy kept on
    # disk instead. A fetch served from cache answers in tens of milliseconds
    # and never sees the stored copy; one that went to the network is what
    # this is for.
    STORED_META_AFTER_S = 0.15

    async def _fetch_meta(self, media_type: MediaType, item_id: str, token: int) -> MetaDetail:
        """The title's meta, standing on the stored copy while a slow fetch runs.

        A meta older than a day is fetched again rather than trusted (an airing
        series gains episodes), and that fetch can be slow -- 3s for one
        Cinemeta meta in a real session, with the throbber up throughout. So
        when it has not answered in STORED_META_AFTER_S, the last copy on disk
        goes on screen, and the fresh one replaces it when it lands. If the
        fresh fetch fails, the stored copy stays: a page from last week beats
        "Couldn't load this title"."""
        fetch = asyncio.ensure_future(self._get_detail(media_type, item_id))
        if self._stored_meta is None:
            return await fetch
        done, _ = await asyncio.wait({fetch}, timeout=self.STORED_META_AFTER_S)
        if done:
            return fetch.result()
        stored = await self._stored_meta(media_type, item_id)
        if stored is not None and token == self._seq and not fetch.done():
            self._show_meta(stored, item_id, media_type)
        try:
            return await fetch
        except GravitasError:
            if stored is not None and self._meta is stored:
                return stored
            raise

    def _show_meta(self, meta: MetaDetail, item_id: str, media_type: MediaType) -> bool:
        """Put `meta` on the page; answers whether the title is episodic.

        Called twice when a stored copy stood in for a slow fetch. The second
        call must not undo what the viewer did in between: an unchanged meta
        changes nothing, and a changed episode list is re-read for the season
        on screen without resetting the season or the selected episode."""
        # Episode-driven flow: no stream fetch until the user picks an episode
        # (streams are per-episode video ids).
        episodic = media_type == "series" and bool(meta.videos) and self._episode_model is not None
        if self._meta == meta and self._meta_for == item_id:
            return episodic
        replacing = self._meta is not None and self._meta_for == item_id and bool(self._seasons)
        self._meta = meta
        self._meta_for = item_id
        if episodic and replacing:
            self._refresh_seasons()
        elif episodic:
            # Before metaChanged, not after: the page decides whether it lists
            # sources inline from hasMeta AND hasEpisodes, and announcing the
            # meta first gave it one synchronous instant of "meta, no episodes"
            # -- enough to bind the source list and unbind it again inside the
            # same call.
            self._build_seasons()
        self.metaChanged.emit()
        return episodic

    def _refresh_seasons(self) -> None:
        """A newer episode list for the series already on screen: re-read it,
        keeping the season the viewer is looking at."""
        current = self._seasons[self._season_idx] if self._seasons else None
        videos = self._meta.videos if self._meta else ()
        distinct = {v.season or 0 for v in videos}
        self._seasons = sorted(season for season in distinct if season != 0)
        if 0 in distinct:
            self._seasons.append(0)
        self._season_idx = self._seasons.index(current) if current in self._seasons else 0
        if self._episode_model is not None and self._seasons:
            self._episode_model.set_videos(self._season_videos(self._seasons[self._season_idx]))
        self._apply_episode_query()
        self.episodesChanged.emit()

    async def _title_streams(self, media_type: MediaType, stream_id: str, token: int) -> None:
        """Fetch and show the sources of the title itself (a film, or a series
        without an episode list)."""
        self._streams_loading = True
        self._sources_failed = False
        self.sourcesChanged.emit()
        try:
            await self._show_stored(media_type, stream_id, token, None)
            streams = await self._resolve_stream(media_type, stream_id)
            if token != self._seq:
                return
            await self._apply_streams(streams, token, None)
        except SourcesUnavailable:
            if token == self._seq:
                self._sources_failed = True
        except NoStreams:
            # a normal empty state (no stream addon configured, or none for
            # this title) — leave Sources empty, don't raise a red error toast
            pass
        except GravitasError as exc:
            if token == self._seq:
                self.errorOccurred.emit(str(exc))
        finally:
            if token == self._seq:
                self._streams_loading = False
                self.sourcesChanged.emit()
                self._settle_pick()

    def set_samples_persist(self, persist: Callable[[], None]) -> None:
        """Wired after construction: the settings controller that owns the
        write does not exist yet when this controller is built."""
        self._on_samples_changed = persist

    async def _show_stored(
        self, type: MediaType, video_id: str, token: int, ep_token: int | None
    ) -> None:
        """Put the last known list on screen while the fresh one is fetched.

        streamsLoading stays true throughout, so the header spinner keeps
        saying the list is not final; the fresh answer replaces it wholesale.
        If the fresh request comes back with nothing (an aggregator whose
        scrapers all timed out answers an empty list), this list stays: rows
        that may still play beat an empty page. No bandwidth probe here -- it
        would hold these rows back for the very seconds they exist to fill.
        """
        if self._stored is None:
            return
        streams = await self._stored(type, video_id)
        if not streams or token != self._seq or (ep_token is not None and ep_token != self._ep_seq):
            return
        self._rows_stored = True
        await self._apply_streams(streams, token, ep_token, measure=False)

    async def _apply_streams(
        self, streams: list[Stream], token: int, ep_token: int | None, *, measure: bool = True
    ) -> None:
        """Hand resolved streams to the model, ordered for this connection.

        With the setting off (or nothing measured yet) rank_streams returns the
        addon's own order untouched, so this is the single path either way --
        there is no second, "unsorted" way for streams to reach the model.

        A cold-start probe is awaited HERE, before anything reaches the model,
        while the page still shows its loading skeletons. Sorting a list the
        user is already reading means rows moving under the cursor, which is
        worse than the extra second: the probe is deadlined at
        PROBE_TIMEOUT_S, and it happens once per link, ever.
        """
        connection = self._connection
        # Either consumer of the estimate is reason enough to measure: the
        # recommendations need a ceiling even when the sort is off, and they
        # are on by default.
        wants_estimate = (
            measure
            and connection is not None
            and (
                connection.enabled
                or (self._recommendations is not None and self._recommendations.enabled)
            )
        )
        if wants_estimate and connection is not None and connection.estimate_kbps() is None:
            await self._probe_bandwidth(streams)
            if token != self._seq or (ep_token is not None and ep_token != self._ep_seq):
                return  # the user moved on while the probe ran
        incompatible = self._incompatible
        if incompatible is not None and incompatible.enabled:
            # Split before ranking, not after: a source the machine cannot
            # display has no place in an order about what plays best.
            self._shown_streams, self._hidden_streams = partition(streams, incompatible.known_bad())
        else:
            self._shown_streams, self._hidden_streams = list(streams), []
        if measure:
            self._rows_stored = False
        await self._render_streams()
        self.sourcesChanged.emit()
        if measure:
            self._warm_top_source()

    def _warm_top_source(self) -> None:
        """Resolve the link most likely to be clicked: the top row, which is
        the recommendation when there is one. A fresh list only -- a stored
        one's links may be long expired, and it is replaced within seconds."""
        if self._warm_link is None or self._stream_model.rowCount() == 0:
            return
        top = self._stream_model.stream_at(0)
        url = top.playable_url
        if url and top.yt_id is None:
            self._warm_link(url, top.proxy_headers)

    async def _render_streams(self) -> None:
        """Rank what is on offer and push it to the model.

        Separate from _apply_streams because revealing the hidden sources
        re-renders the SAME list -- nothing is refetched, nothing is measured
        again, and the probe must not run a second time for a click on
        "show hidden".

        The ranking runs on a worker thread. It is pure work over frozen
        streams, and on an aggregator's list it is not small: 145 sources
        took ~5ms (recommend 3.1, rank_streams 1.8, measured), twice per open
        -- the stored list, then the fresh one -- on the thread that drives
        every QML animation, where a frame at 144Hz is 6.9ms. Everything it
        reads from Qt or from mutable state is snapshotted here first, and a
        result that a newer render or a reset has overtaken is dropped.
        """
        self._render_seq += 1
        seq = self._render_seq
        inputs = self._render_inputs()
        rendered = await asyncio.to_thread(_rank_for_display, inputs)
        if seq != self._render_seq:
            return
        self._stream_model.set_streams(*rendered)

    def _clear_streams(self) -> None:
        """Empty the list, and overtake any ranking still on its thread. A
        pick waiting on the old list is answered -1: that page moved on."""
        self._render_seq += 1
        self._stream_model.set_streams([])
        self._rows_stored = False
        if self._pending_pick is not None:
            self._pending_pick = None
            self.freshSourceReady.emit(-1)

    def _render_inputs(self) -> _RenderInputs:
        connection = self._connection
        # The sort is opt-in, so with it off rank_streams and the Downscaled
        # chip are fed the "nothing measured" arguments and leave the addon's
        # own order alone. The recommendations read the real numbers whenever
        # they are on -- marking the best row is not reordering the list.
        sort_on = connection is not None and connection.enabled
        settings = self._recommendations
        recommend_on = settings is not None and settings.enabled
        wanted = sort_on or recommend_on
        return _RenderInputs(
            shown=tuple(self._shown_streams),
            revealed=tuple(self._hidden_streams) if self._reveal_hidden else (),
            sort_on=sort_on,
            kbps=connection.estimate_kbps() if wanted and connection is not None else None,
            # A Qt query: read here, on the GUI thread, never on the worker.
            height=self._display_height() if wanted and self._display_height is not None else 0,
            runtime_s=self._runtime_seconds(),
            recommend_limit=settings.limit if settings is not None and recommend_on else None,
            known_bad=(
                self._incompatible.known_bad() if self._incompatible is not None else frozenset()
            ),
            strained=(
                self._capability.strained_keys() if self._capability is not None else frozenset()
            ),
        )

    def _runtime_seconds(self) -> float | None:
        return parse_runtime_seconds(self._meta.runtime or "") if self._meta else None

    async def _probe_bandwidth(self, streams: list[Stream]) -> None:
        """Measure this link once, against the first source the user could have
        played anyway. Records the sample; sorting is the caller's business."""
        probe, connection = self._probe, self._connection
        if probe is None or connection is None:
            return
        wanted = connection.enabled or (
            self._recommendations is not None and self._recommendations.enabled
        )
        if not connection.needs_probe(wanted=wanted):
            return
        target = next((s for s in streams if s.playable_url), None)
        url = target.playable_url if target is not None else None
        if target is None or url is None:
            return
        kbps = await probe.measure_kbps(url, target.proxy_headers)
        if kbps is None:
            return  # unmeasurable link: the list keeps the addon's order
        connection.record(kbps)
        if self._on_samples_changed is not None:
            self._on_samples_changed()

    async def _load_ratings(self, token: int, imdb_id: str, media_type: MediaType) -> None:
        get_ratings = self._get_ratings
        if get_ratings is None:
            return
        ratings = await get_ratings(imdb_id, media_type)
        if token != self._seq:
            return  # a newer load started; drop this stale result
        self._ratings = ratings
        # The id QML asked for, not meta.id: the page compares it against the
        # id it passed to load().
        self._ratings_id = self._media_id
        self.ratingsChanged.emit()


def _following_video(videos: tuple[Video, ...], video_id: str) -> Video | None:
    """The episode after `video_id` in watching order: the next episode of the
    season, else the first of the next season. Specials only follow specials --
    finishing a regular episode is not a reason to fetch a special."""
    current = next((v for v in videos if v.id == video_id), None)
    if current is None:
        return None
    special = (current.season or 0) == 0
    ordered = sorted(
        (v for v in videos if ((v.season or 0) == 0) == special),
        key=lambda v: (v.season or 0, v.episode or 0),
    )
    position = next(i for i, v in enumerate(ordered) if v.id == video_id)
    return ordered[position + 1] if position + 1 < len(ordered) else None
