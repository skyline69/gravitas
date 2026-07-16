import asyncio

from gravitas.domain.errors import AddonUnreachable, NoStreams
from gravitas.domain.models import MetaDetail, Ratings, Stream
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.stream_list_model import StreamListModel


class FakeGetDetail:
    async def __call__(self, type, item_id):
        return MetaDetail(
            id=item_id,
            type="movie",
            name="Film",
            description="d",
            poster="p",
            background="b",
            videos=(),
            logo="l",
            year="2026",
            runtime="102 min",
            imdb_rating="7.5",
            genres=("Animation", "Comedy"),
            cast=("Tom Hanks",),
            directors=("Dir",),
        )


class FakeResolve:
    async def __call__(self, type, item_id):
        return [
            Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None)
        ]


class FakeGetRatings:
    def __init__(self, result: Ratings) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, imdb_id: str, media_type: str) -> Ratings:
        self.calls.append((imdb_id, media_type))
        return self._result


async def _drain_pending_tasks() -> None:
    """Let fire-and-forget tasks (scheduled via asyncio.ensure_future) run.

    FakeGetRatings awaits no real I/O, so two loop turns are enough for its
    task to reach completion and emit ratingsChanged."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class GatedGetDetail:
    """Each call blocks on a per-id gate so the test controls completion order."""

    def __init__(self) -> None:
        self.gates: dict[str, asyncio.Event] = {}
        self.entered = asyncio.Event()

    async def __call__(self, type, item_id):
        ev = asyncio.Event()
        self.gates[item_id] = ev
        self.entered.set()
        await ev.wait()
        return MetaDetail(
            id=item_id,
            type="movie",
            name=item_id,
            description="d",
            poster=None,
            background=None,
            videos=(),
        )


class FailGetDetail:
    async def __call__(self, *a, **k):
        raise AddonUnreachable("boom")


class FailResolve:
    async def __call__(self, *a, **k):
        raise AddonUnreachable("no streams")


class NoStreamsResolve:
    async def __call__(self, *a, **k):
        raise NoStreams("no direct-URL streams for tt1")


async def test_load_populates_meta(qapp: object) -> None:
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), model)  # type: ignore[arg-type]
    changes: list[int] = []
    ctl.metaChanged.connect(lambda: changes.append(1))

    await ctl.load("movie", "tt1")

    assert ctl.title == "Film"
    assert ctl.description == "d"
    assert ctl.logo == "l"
    assert ctl.background == "b"
    assert ctl.year == "2026"
    assert ctl.runtime == "102 min"
    assert ctl.imdbRating == "7.5"
    assert list(ctl.genres) == ["Animation", "Comedy"]
    assert list(ctl.cast) == ["Tom Hanks"]
    assert list(ctl.directors) == ["Dir"]
    assert ctl.hasMeta is True
    assert model.rowCount() == 1
    assert changes


async def test_loads_ratings_after_meta(qapp: object) -> None:
    ratings = Ratings(rotten_tomatoes="87", rotten_tomatoes_fresh=True, letterboxd="4.1")
    get_ratings = FakeGetRatings(ratings)
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        get_ratings=get_ratings,  # type: ignore[arg-type]
    )

    await ctl.load("movie", "tt123")
    await _drain_pending_tasks()

    assert get_ratings.calls == [("tt123", "movie")]
    assert ctl.rottenTomatoes == "87"
    assert ctl.rottenTomatoesFresh is True
    assert ctl.letterboxd == "4.1"


async def test_skips_ratings_for_non_imdb_id(qapp: object) -> None:
    get_ratings = FakeGetRatings(Ratings())
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        get_ratings=get_ratings,  # type: ignore[arg-type]
    )

    await ctl.load("movie", "tmdb:99")  # not a tt id
    await _drain_pending_tasks()

    assert get_ratings.calls == []
    assert ctl.rottenTomatoes == ""


async def test_ratings_default_empty_without_resolver(qapp: object) -> None:
    ctl = DetailController(FakeGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]

    await ctl.load("movie", "tt123")

    assert ctl.rottenTomatoes == ""
    assert ctl.rottenTomatoesFresh is False
    assert ctl.letterboxd == ""


async def test_load_error_emits(qapp: object) -> None:
    ctl = DetailController(FailGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("movie", "tt1")
    assert errors == ["boom"]
    assert ctl.hasMeta is False


async def test_stale_load_does_not_clobber_newer_one(qapp: object) -> None:
    # click A (slow), go back, click B (completes first): B must win even
    # though A's fetch finishes LAST
    gated = GatedGetDetail()
    model = StreamListModel()
    ctl = DetailController(gated, FakeResolve(), model)  # type: ignore[arg-type]

    task_a = asyncio.ensure_future(ctl.load("movie", "A"))
    await gated.entered.wait()
    gated.entered.clear()
    task_b = asyncio.ensure_future(ctl.load("movie", "B"))
    await gated.entered.wait()

    # B completes first, then the stale A completes
    gated.gates["B"].set()
    await task_b
    gated.gates["A"].set()
    await task_a

    assert ctl.title == "B"  # newest selection wins, not the last-to-finish


async def test_no_streams_is_silent_empty_state(qapp: object) -> None:
    # meta loads, but there are no direct streams -> Sources stays empty and
    # NO error toast is emitted (this is a normal "nothing configured" state)
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), NoStreamsResolve(), model)  # type: ignore[arg-type]
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)

    await ctl.load("movie", "tt1")

    assert errors == []
    assert ctl.hasMeta is True
    assert model.rowCount() == 0


async def test_stale_streams_cleared_when_resolve_fails(qapp: object) -> None:
    # first item resolves streams; second item's meta loads but its stream
    # fetch fails -> the previous item's streams must not linger
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), model)  # type: ignore[arg-type]
    await ctl.load("movie", "tt1")
    assert model.rowCount() == 1

    ctl._resolve_stream = FailResolve()  # type: ignore[assignment]
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("movie", "tt2")

    assert errors == ["no streams"]
    assert model.rowCount() == 0


class SeriesGetDetail:
    async def __call__(self, type, item_id):
        from gravitas.domain.models import Video

        return MetaDetail(
            id=item_id,
            type="series",
            name="Show",
            description="d",
            poster=None,
            background=None,
            videos=(
                Video(id="tt1:0:1", title="Special", season=0, episode=1),
                Video(id="tt1:2:1", title="S2 opener", season=2, episode=1),
                Video(id="tt1:1:2", title="Two", season=1, episode=2, thumbnail="http://t/2.jpg"),
                Video(id="tt1:1:1", title="One", season=1, episode=1, overview="Pilot."),
            ),
        )


def _series_ctl() -> tuple[DetailController, StreamListModel, EpisodeListModel]:
    stream_model = StreamListModel()
    episode_model = EpisodeListModel()
    ctl = DetailController(SeriesGetDetail(), FakeResolve(), stream_model, episode_model)  # type: ignore[arg-type]
    return ctl, stream_model, episode_model


async def test_series_load_builds_seasons_no_stream_fetch(qapp: object) -> None:
    ctl, stream_model, episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    # regular seasons ascending, Specials last
    assert list(ctl.seasonOptions) == ["Season 1", "Season 2", "Specials"]
    assert ctl.seasonIndex == 0
    # season 1 episodes sorted by episode number
    assert episode_model.rowCount() == 2
    assert episode_model.data(episode_model.index(0, 0), EpisodeListModel.TitleRole) == "One"
    assert episode_model.data(episode_model.index(0, 0), EpisodeListModel.OverviewRole) == "Pilot."
    # no auto stream fetch for series
    assert stream_model.rowCount() == 0
    assert ctl.sourcesLabel == "Sources"
    assert ctl.selectedEpisodeId == ""


async def test_select_season_repopulates(qapp: object) -> None:
    ctl, _stream_model, episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    ctl.selectSeason(2)  # Specials
    assert ctl.seasonIndex == 2
    assert episode_model.rowCount() == 1
    assert episode_model.data(episode_model.index(0, 0), EpisodeListModel.TitleRole) == "Special"


async def test_select_episode_resolves_streams_and_labels(qapp: object) -> None:
    ctl, stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:1", 1, 1, "One")
    assert stream_model.rowCount() == 1
    assert ctl.sourcesLabel == "Sources — S1E1 · One"
    assert ctl.selectedEpisodeId == "tt1:1:1"
    assert ctl.streamsLoading is False


async def test_select_episode_no_streams_stays_silent(qapp: object) -> None:
    stream_model = StreamListModel()
    episode_model = EpisodeListModel()
    ctl = DetailController(SeriesGetDetail(), NoStreamsResolve(), stream_model, episode_model)  # type: ignore[arg-type]
    errors: list[str] = []
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:1", 1, 1, "One")
    assert errors == []
    assert stream_model.rowCount() == 0
    assert ctl.streamsLoading is False


async def test_movie_flow_unchanged_by_episode_model(qapp: object) -> None:
    stream_model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), stream_model, EpisodeListModel())  # type: ignore[arg-type]
    await ctl.load("movie", "tt1")
    assert stream_model.rowCount() == 1
    assert list(ctl.seasonOptions) == []


async def test_media_context_for_a_movie(qapp: object) -> None:
    ctl = DetailController(FakeGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    await ctl.load("movie", "tt2")
    assert ctl.mediaContext() == {
        "mediaId": "tt2",
        "videoId": "",
        "type": "movie",
        "name": "Film",
        "poster": "p",
        "label": "",
    }


async def test_media_context_for_a_selected_episode(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "Two")
    assert ctl.mediaContext() == {
        "mediaId": "tt1",
        "videoId": "tt1:1:2",
        "type": "series",
        "name": "Show",
        "poster": "",
        "label": "S1E2 · Two",
    }


async def test_episode_label_without_a_title(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "")
    assert ctl.mediaContext()["label"] == "S1E2"


async def test_load_resets_the_context(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "Two")
    await ctl.load("series", "tt1")
    # A stale episode id would attribute the next play to the wrong episode.
    assert ctl.mediaContext()["videoId"] == ""
    assert ctl.mediaContext()["label"] == ""


async def test_episode_model_is_bound_to_the_series(qapp: object) -> None:
    ctl, _stream_model, episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    # Episode progress is keyed by (media_id, video_id). Without this the model
    # looks every episode up under an empty media id and every bar reads zero.
    assert episode_model.media_id == "tt1"


class _CwStore:
    def __init__(self, entries: list[object]) -> None:
        self.entries = entries

    def load_all(self) -> list[object]:
        return list(self.entries)

    def save(self, entry: object) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None: ...
    def clear(self) -> None: ...


def _progress_entry(video_id: str, **kw: object):  # type: ignore[no-untyped-def]
    from gravitas.domain.models import PlaybackProgress

    base: dict[str, object] = {
        "media_id": "tt1",
        "video_id": video_id,
        "type": "series",
        "name": "Show",
        "poster": None,
        "label": "S1E2 · Two",
        "position": 300.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def _series_ctl_with_progress(entries: list[object]) -> DetailController:
    from gravitas.application.watch_progress import WatchProgressRepository

    repo = WatchProgressRepository(_CwStore(entries))  # type: ignore[arg-type]
    return DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        EpisodeListModel(repo),
        repo,
    )


async def test_series_load_preselects_the_in_progress_episode(qapp: object) -> None:
    ctl = _series_ctl_with_progress([_progress_entry("tt1:1:2")])
    await ctl.load("series", "tt1")
    # Arriving from Continue Watching, the episode you were on is already
    # selected and its sources are loading.
    assert ctl.selectedEpisodeId == "tt1:1:2"
    assert ctl.mediaContext()["videoId"] == "tt1:1:2"


async def test_series_load_selects_the_season_of_the_in_progress_episode(
    qapp: object,
) -> None:
    # tt1:2:1 is in season 2; the season box must follow, or the selected
    # episode is not even in the visible list.
    ctl = _series_ctl_with_progress([_progress_entry("tt1:2:1", label="S2E1")])
    await ctl.load("series", "tt1")
    assert ctl.selectedEpisodeId == "tt1:2:1"
    assert list(ctl.seasonOptions)[ctl.seasonIndex] == "Season 2"


async def test_series_load_without_progress_selects_nothing(qapp: object) -> None:
    ctl = _series_ctl_with_progress([])
    await ctl.load("series", "tt1")
    assert ctl.selectedEpisodeId == ""
    assert ctl.seasonIndex == 0


async def test_series_load_ignores_a_watched_episode(qapp: object) -> None:
    # A finished episode is not something to resume into.
    ctl = _series_ctl_with_progress([_progress_entry("tt1:1:2", watched=True, position=0.0)])
    await ctl.load("series", "tt1")
    assert ctl.selectedEpisodeId == ""


async def test_series_load_ignores_an_unknown_episode_id(qapp: object) -> None:
    # Stale progress for an episode this meta no longer lists must not select
    # a phantom, nor leave the season box pointing nowhere.
    ctl = _series_ctl_with_progress([_progress_entry("tt1:9:9")])
    await ctl.load("series", "tt1")
    assert ctl.selectedEpisodeId == ""
    assert ctl.seasonIndex == 0


async def test_movie_load_is_unaffected_by_progress(qapp: object) -> None:
    from gravitas.application.watch_progress import WatchProgressRepository

    repo = WatchProgressRepository(_CwStore([_progress_entry("", media_id="tt2", type="movie")]))  # type: ignore[arg-type]
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),
        StreamListModel(),
        EpisodeListModel(repo),
        repo,
    )
    await ctl.load("movie", "tt2")
    assert ctl.selectedEpisodeId == ""


async def test_movie_streams_use_default_video_id_when_it_differs() -> None:
    # behaviorHints.defaultVideoId names the video whose streams represent the
    # title; an addon whose stream id differs from the meta id would otherwise
    # be asked for the wrong one.
    asked: list[str] = []

    class Meta:
        async def __call__(self, type: str, id: str) -> MetaDetail:
            return MetaDetail(
                id=id,
                type="movie",
                name="M",
                description=None,
                poster=None,
                background=None,
                videos=(),
                default_video_id="yt:xyz",
            )

    class Streams:
        async def __call__(self, type: str, id: str) -> list[Stream]:
            asked.append(id)
            return [Stream(name="s", title="t", url="http://v", info_hash=None, file_idx=None)]

    controller = DetailController(Meta(), Streams(), StreamListModel(), EpisodeListModel(), None)
    await controller.load("movie", "tt1")

    assert asked == ["yt:xyz"]


async def test_movie_streams_fall_back_to_media_id_without_default_video_id() -> None:
    asked: list[str] = []

    class Meta:
        async def __call__(self, type: str, id: str) -> MetaDetail:
            return MetaDetail(
                id=id,
                type="movie",
                name="M",
                description=None,
                poster=None,
                background=None,
                videos=(),
            )

    class Streams:
        async def __call__(self, type: str, id: str) -> list[Stream]:
            asked.append(id)
            return []

    controller = DetailController(Meta(), Streams(), StreamListModel(), EpisodeListModel(), None)
    await controller.load("movie", "tt1")

    assert asked == ["tt1"]
