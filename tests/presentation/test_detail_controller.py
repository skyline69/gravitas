import asyncio
from collections.abc import Callable

from gravitas.domain.errors import AddonUnreachable, NoStreams
from gravitas.domain.models import MetaDetail, Ratings, Stream, Video
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


async def _drain_until(condition: Callable[[], bool], turns: int = 100) -> None:
    """Turn the loop until `condition` holds, or give up after `turns`.

    For waits whose length is an implementation detail -- how many hops a
    load takes to finish -- rather than a fixed count that the next await
    added anywhere silently breaks."""
    for _ in range(turns):
        if condition():
            return
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


async def test_ratings_are_tagged_with_the_item_they_describe(qapp: object) -> None:
    """The Detail page shows a rating only while `ratingsFor` names its own item.

    load() is an asyncSlot, so QML's call returns a loop turn before the reset
    inside it runs: a page built for B binds against A's ratings in between.
    Tagging them lets the page tell the two apart and stay blank until its own
    numbers land, instead of flashing the previous film's pill."""
    get_ratings = FakeGetRatings(Ratings(rotten_tomatoes="87", letterboxd="4.1"))
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        get_ratings=get_ratings,  # type: ignore[arg-type]
    )
    assert ctl.ratingsFor == ""  # nothing loaded yet

    await ctl.load("movie", "tt_A")
    await _drain_pending_tasks()
    assert ctl.ratingsFor == "tt_A"
    assert ctl.rottenTomatoes == "87"

    # A page built for B, while the controller still holds A's ratings, must
    # not treat them as its own.
    assert ctl.ratingsFor != "tt_B"

    # Every tag the page could observe while B loads. The reset must clear it
    # first, so B's page never sees A's ratings wearing B's name.
    seen: list[str] = []
    ctl.ratingsChanged.connect(lambda: seen.append(ctl.ratingsFor))

    await ctl.load("movie", "tt_B")
    await _drain_pending_tasks()

    assert seen == ["", "tt_B"]
    assert ctl.ratingsFor == "tt_B"
    assert ctl.rottenTomatoes == "87"


async def test_ratings_tag_stays_empty_when_none_load(qapp: object) -> None:
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        get_ratings=FakeGetRatings(Ratings()),  # type: ignore[arg-type]
    )

    await ctl.load("movie", "tt123")
    await _drain_pending_tasks()

    # Tagged even when empty: the page asked, the answer was "no ratings", and
    # it must not keep waiting on a pill that is never coming.
    assert ctl.ratingsFor == "tt123"
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


async def test_episode_search_spans_seasons_and_leaves_the_season_alone(qapp: object) -> None:
    stream_model = StreamListModel()
    episode_model = EpisodeListModel()
    search_model = EpisodeListModel()
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        stream_model,
        episode_model,
        episode_search_model=search_model,
    )
    await ctl.load("series", "tt1")

    def titles(model: EpisodeListModel) -> list[str]:
        return [
            model.data(model.index(row, 0), EpisodeListModel.TitleRole)
            for row in range(model.rowCount())
        ]

    ctl.setEpisodeQuery("e1")
    assert ctl.episodeQuery == "e1"
    assert titles(search_model) == ["One", "S2 opener", "Special"], "every season, Specials last"
    assert titles(episode_model) == ["One", "Two"], "the season on screen is untouched"
    assert search_model.media_id == "tt1", "progress bars read the right series"

    ctl.selectSeason(1)
    assert ctl.episodeQuery == "", "picking a season ends the search"
    assert search_model.rowCount() == 0

    ctl.setEpisodeQuery("opener")
    await ctl.load("series", "tt1")
    assert ctl.episodeQuery == "", "a new page starts without a search"
    assert search_model.rowCount() == 0


async def test_select_episode_resolves_streams_and_labels(qapp: object) -> None:
    ctl, stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:1", 1, 1, "One")
    assert stream_model.rowCount() == 1
    assert ctl.sourcesLabel == "Sources: S1E1 · One"
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

    def load_forgotten(self) -> list[tuple[str, str, int]]:
        return []

    def save_forgotten(self, media_id: str, video_id: str, deleted_at: int) -> None: ...


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


class MultiResolve:
    """Three sources of different weights, in an order no ranking would pick."""

    def __init__(self) -> None:
        self.streams = [
            Stream(
                name="1080p · 8 Mbps",
                title="",
                url="http://s/1080.mkv",
                info_hash=None,
                file_idx=None,
            ),
            Stream(
                name="4K · 60 Mbps",
                title="",
                url="http://s/4k-heavy.mkv",
                info_hash=None,
                file_idx=None,
            ),
            Stream(
                name="4K · 20 Mbps",
                title="",
                url="http://s/4k.mkv",
                info_hash=None,
                file_idx=None,
            ),
        ]

    async def __call__(self, type, item_id):
        return list(self.streams)


class FakeProbe:
    """A probe whose answer can be held back, so a test can observe the list
    between "rendered" and "re-sorted" -- the two states a real probe, which
    takes seconds, puts a user through."""

    def __init__(self, kbps: int | None, *, gated: bool = False) -> None:
        self._kbps = kbps
        self.calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()

    async def measure_kbps(self, url: str, headers=()) -> int | None:
        self.calls.append((url, tuple(headers)))
        await self.gate.wait()
        return self._kbps


def _names(model: StreamListModel) -> list[str]:
    return [model.stream_at(i).name for i in range(model.rowCount())]


async def test_sources_keep_the_addon_order_with_the_setting_off() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = False
    connection.record(30_000)
    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
    )

    await ctl.load("movie", "tt1")

    assert _names(model) == ["1080p · 8 Mbps", "4K · 60 Mbps", "4K · 20 Mbps"]


async def test_sources_are_banded_by_the_measured_connection() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True
    for _ in range(6):
        connection.record(30_000)  # ~30 Mbps line
    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
    )

    await ctl.load("movie", "tt1")

    assert _names(model) == ["4K · 20 Mbps", "1080p · 8 Mbps", "4K · 60 Mbps"]
    over = [
        model.data(model.index(i, 0), StreamListModel.OverBudgetRole)
        for i in range(model.rowCount())
    ]
    assert over == [False, False, True]


async def test_a_cold_start_sorts_before_the_list_is_shown() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True  # on, but nothing measured on this link yet
    probe = FakeProbe(30_000, gated=True)
    model = StreamListModel()
    persists: list[int] = []
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
        probe=probe,  # type: ignore[arg-type]
        on_samples_changed=lambda: persists.append(1),
    )

    loading = asyncio.ensure_future(ctl.load("movie", "tt1"))
    await _drain_pending_tasks()
    # While the probe is in flight the page is still "loading sources": rows
    # must not appear and then rearrange themselves under the cursor.
    assert model.rowCount() == 0
    assert ctl.streamsLoading is True

    probe.gate.set()
    await loading

    assert [call[0] for call in probe.calls] == ["http://s/1080.mkv"]
    assert _names(model) == ["4K · 20 Mbps", "1080p · 8 Mbps", "4K · 60 Mbps"]
    assert ctl.streamsLoading is False
    assert persists == [1]  # the sample is written, once


async def test_an_unmeasurable_link_leaves_the_list_alone() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True
    probe = FakeProbe(None)  # host refused, timed out, whatever
    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
        probe=probe,  # type: ignore[arg-type]
    )

    await ctl.load("movie", "tt1")
    await _drain_pending_tasks()

    assert _names(model) == ["1080p · 8 Mbps", "4K · 60 Mbps", "4K · 20 Mbps"]
    assert connection.samples == ()


async def test_a_measured_link_is_never_probed_again() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True
    connection.record(50_000)
    probe = FakeProbe(30_000)
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        connection=connection,
        probe=probe,  # type: ignore[arg-type]
    )

    await ctl.load("movie", "tt1")
    await _drain_pending_tasks()

    assert probe.calls == []


async def test_an_episode_pick_during_the_probe_wins() -> None:
    # The probe holds the first episode's list back; the user picks another
    # episode meanwhile. The stale list must never land.
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True
    probe = FakeProbe(30_000, gated=True)
    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
        probe=probe,  # type: ignore[arg-type]
    )

    first = asyncio.ensure_future(ctl.selectEpisode("tt1:1:1", 1, 1, "One"))
    await _drain_pending_tasks()
    ctl._ep_seq += 1  # the user clicked another episode
    probe.gate.set()
    await first

    assert model.rowCount() == 0


async def test_the_screen_caps_the_sort_and_marks_downscaled_rows() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True
    for _ in range(6):
        connection.record(200_000)  # bandwidth is not the constraint here
    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
        display_height=lambda: 1080,
    )

    await ctl.load("movie", "tt1")

    # Both 4K sources are downscaled to 1080p, so the native 1080p leads.
    assert _names(model) == ["1080p · 8 Mbps", "4K · 60 Mbps", "4K · 20 Mbps"]
    assert [
        model.data(model.index(i, 0), StreamListModel.OversizedRole)
        for i in range(model.rowCount())
    ] == [False, True, True]


async def test_the_screen_is_ignored_while_the_setting_is_off() -> None:
    from gravitas.application.connection_speed import ConnectionSpeed

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = False
    model = StreamListModel()
    asked: list[int] = []
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        model,
        connection=connection,
        display_height=lambda: asked.append(1) or 1080,  # type: ignore[func-returns-value]
    )

    await ctl.load("movie", "tt1")

    assert asked == []  # nothing about the screen is consulted
    assert _names(model) == ["1080p · 8 Mbps", "4K · 60 Mbps", "4K · 20 Mbps"]
    assert [
        model.data(model.index(i, 0), StreamListModel.OversizedRole)
        for i in range(model.rowCount())
    ] == [False, False, False]


async def test_a_unanimous_downscale_is_not_worth_a_chip() -> None:
    # A laptop panel: every 4K source is downscaled, so marking them all says
    # nothing about any of them.
    from gravitas.application.connection_speed import ConnectionSpeed

    class AllFourK:
        async def __call__(self, type, item_id):
            return [
                Stream(
                    name=f"4K · {mbps} Mbps",
                    title="",
                    url=f"http://s/{mbps}.mkv",
                    info_hash=None,
                    file_idx=None,
                )
                for mbps in (26, 25, 24)
            ]

    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = True
    for _ in range(6):
        connection.record(200_000)
    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        AllFourK(),  # type: ignore[arg-type]
        model,
        connection=connection,
        display_height=lambda: 1912,
    )

    await ctl.load("movie", "tt1")

    assert model.rowCount() == 3
    assert [model.data(model.index(i, 0), StreamListModel.OversizedRole) for i in range(3)] == [
        False,
        False,
        False,
    ]
    # Ordering is untouched by dropping the mark: bitrate still decides.
    assert _names(model) == ["4K · 26 Mbps", "4K · 25 Mbps", "4K · 24 Mbps"]


class DvResolve:
    """One profile-8 source (DV over HDR10) and one bare-DV suspect."""

    GOOD = "Silo S01E01 HEVC DV · HDR10 · 26.4 Mbps"
    SUSPECT = "Silo S01E01 HEVC DV · Atmos · 26.4 Mbps"

    async def __call__(self, type, item_id):
        return [
            Stream(
                name=DvResolve.GOOD, title="", url="http://s/a.mkv", info_hash=None, file_idx=None
            ),
            Stream(
                name=DvResolve.SUSPECT,
                title="",
                url="http://s/b.mkv",
                info_hash=None,
                file_idx=None,
            ),
        ]


def _dv_controller(model: StreamListModel, incompatible):
    return DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        DvResolve(),  # type: ignore[arg-type]
        model,
        incompatible=incompatible,
    )


async def test_incompatible_sources_are_held_back_and_counted() -> None:
    from gravitas.application.compatibility import IncompatibleSources

    incompatible = IncompatibleSources()
    incompatible.enabled = True
    model = StreamListModel()
    ctl = _dv_controller(model, incompatible)

    await ctl.load("movie", "tt1")

    assert _names(model) == [DvResolve.GOOD]
    assert ctl.hiddenSourceCount == 1


async def test_the_filter_is_off_until_asked_for() -> None:
    from gravitas.application.compatibility import IncompatibleSources

    incompatible = IncompatibleSources()  # enabled defaults to False
    model = StreamListModel()
    ctl = _dv_controller(model, incompatible)

    await ctl.load("movie", "tt1")

    assert len(_names(model)) == 2
    assert ctl.hiddenSourceCount == 0


async def test_hidden_sources_can_be_revealed_and_land_last() -> None:
    from gravitas.application.compatibility import IncompatibleSources

    incompatible = IncompatibleSources()
    incompatible.enabled = True
    model = StreamListModel()
    ctl = _dv_controller(model, incompatible)
    await ctl.load("movie", "tt1")

    await ctl.showHiddenSources()

    assert _names(model) == [DvResolve.GOOD, DvResolve.SUSPECT]
    # The offer is no longer pending once it has been taken.
    assert ctl.hiddenSourceCount == 0


async def test_a_new_list_hides_again() -> None:
    from gravitas.application.compatibility import IncompatibleSources

    incompatible = IncompatibleSources()
    incompatible.enabled = True
    model = StreamListModel()
    ctl = _dv_controller(model, incompatible)
    await ctl.load("movie", "tt1")
    await ctl.showHiddenSources()

    await ctl.load("movie", "tt2")

    assert _names(model) == [DvResolve.GOOD]
    assert ctl.hiddenSourceCount == 1


async def test_a_learned_verdict_hides_a_source_the_label_cleared() -> None:
    from gravitas.application.compatibility import IncompatibleSources

    incompatible = IncompatibleSources()
    incompatible.enabled = True
    incompatible.remember(DvResolve.GOOD, "")  # mpv caught it playing as profile 5
    model = StreamListModel()
    ctl = _dv_controller(model, incompatible)

    await ctl.load("movie", "tt1")

    # Both are out now, and partition() refuses to empty the list, so the
    # user still gets something to look at.
    assert len(_names(model)) == 2
    assert ctl.hiddenSourceCount == 0


async def test_opening_a_movie_starts_its_streams_with_the_meta(qapp: object) -> None:
    prefetched: list[tuple[str, str]] = []
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        prefetch=lambda type, id: prefetched.append((type, id)),
    )
    await ctl.load("movie", "tt1")
    assert prefetched == [("movie", "tt1")]


async def test_opening_a_series_prefetches_the_likely_episode(qapp: object) -> None:
    # No progress: the first episode of the season on screen, not a Special
    # or whichever video the addon happened to list first.
    prefetched: list[tuple[str, str]] = []
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        EpisodeListModel(),
        prefetch=lambda type, id: prefetched.append((type, id)),
    )
    await ctl.load("series", "tt1")
    assert prefetched == [("series", "tt1:1:1")]

    # The pointer resting on a row is a guess, and takes the capped path.
    ctl.prefetchEpisode("tt1:1:2")
    assert prefetched == [("series", "tt1:1:1")]


async def test_hovering_episode_rows_only_guesses(qapp: object) -> None:
    guessed: list[tuple[str, str]] = []
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        EpisodeListModel(),
        speculate=lambda type, id: guessed.append((type, id)),
    )
    await ctl.load("series", "tt1")
    for row in ("tt1:1:1", "tt1:1:2", "tt1:2:1"):
        ctl.prefetchEpisode(row)
    assert guessed == [("series", "tt1:1:1"), ("series", "tt1:1:2"), ("series", "tt1:2:1")]


async def test_last_known_sources_show_while_the_fresh_ones_load(qapp: object) -> None:
    gate = asyncio.Event()
    stale = Stream(name="old", title="t", url="http://old", info_hash=None, file_idx=None)
    fresh = Stream(name="new", title="t", url="http://new", info_hash=None, file_idx=None)

    class SlowResolve:
        async def __call__(self, type: str, id: str) -> list[Stream]:
            await gate.wait()
            return [fresh]

    async def stored(type: str, id: str) -> list[Stream]:
        return [stale]

    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        SlowResolve(),  # type: ignore[arg-type]
        model,
        stored=stored,
    )
    load = asyncio.ensure_future(ctl.load("movie", "tt1"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert [model.stream_at(i).name for i in range(model.rowCount())] == ["old"]
    assert ctl.streamsLoading is True, "the placeholder must not read as the final list"

    gate.set()
    await load
    assert [model.stream_at(i).name for i in range(model.rowCount())] == ["new"]
    assert ctl.streamsLoading is False


async def test_last_known_sources_stay_when_the_fresh_answer_is_empty(qapp: object) -> None:
    # An aggregator whose scrapers all timed out answers with nothing; rows
    # that may still play beat an empty page.
    stale = Stream(name="old", title="t", url="http://old", info_hash=None, file_idx=None)

    async def stored(type: str, id: str) -> list[Stream]:
        return [stale]

    model = StreamListModel()
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        NoStreamsResolve(),  # type: ignore[arg-type]
        model,
        EpisodeListModel(),
        stored=stored,
    )
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:1", 1, 1, "One")
    assert model.rowCount() == 1
    assert ctl.streamsLoading is False


async def test_a_stored_row_clicked_early_plays_the_fresh_link(qapp: object) -> None:
    # A stored link can be bound to an address this machine no longer has
    # (ElfHosted served a "Wrong IP" clip for one): a click on a stored row
    # waits for the fresh list and plays the same release's new link.
    gate = asyncio.Event()
    other = Stream(name="720p", title="b", url="http://new-b", info_hash=None, file_idx=None)
    stale = Stream(name="4K", title="a", url="http://old-a", info_hash=None, file_idx=None)
    fresh = Stream(name="4K", title="a", url="http://new-a", info_hash=None, file_idx=None)

    class SlowResolve:
        async def __call__(self, type: str, id: str) -> list[Stream]:
            await gate.wait()
            return [other, fresh]

    async def stored(type: str, id: str) -> list[Stream]:
        return [stale]

    model = StreamListModel()
    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        SlowResolve(),  # type: ignore[arg-type]
        model,
        stored=stored,
    )
    ready: list[int] = []
    ctl.freshSourceReady.connect(ready.append)
    load = asyncio.ensure_future(ctl.load("movie", "tt1"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert ctl.sourcesStored is True
    ctl.pickWhenFresh(0)
    assert ready == [], "nothing plays off the stored list"

    gate.set()
    await load
    assert ctl.sourcesStored is False
    assert len(ready) == 1
    assert ctl.sourceAt(ready[0])["url"] == "http://new-a"


async def test_a_pick_on_a_list_that_stayed_stored_plays_it(qapp: object) -> None:
    # The fresh request came back empty, so the stored rows stay: better a
    # link that may still play than nothing.
    stale = Stream(name="old", title="t", url="http://old", info_hash=None, file_idx=None)

    async def stored(type: str, id: str) -> list[Stream]:
        return [stale]

    model = StreamListModel()
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        NoStreamsResolve(),  # type: ignore[arg-type]
        model,
        EpisodeListModel(),
        stored=stored,
    )
    ready: list[int] = []
    ctl.freshSourceReady.connect(ready.append)
    await ctl.load("series", "tt1")
    ctl._rows_stored = True
    ctl._streams_loading = True
    model.set_streams([stale])
    ctl.pickWhenFresh(0)
    ctl._streams_loading = False
    ctl._settle_pick()
    assert ready == [0]
    assert ctl.sourceAt(0)["url"] == "http://old"


async def test_a_pick_is_dropped_when_the_page_moves_on(qapp: object) -> None:
    stale = Stream(name="old", title="t", url="http://old", info_hash=None, file_idx=None)
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), model)  # type: ignore[arg-type]
    ready: list[int] = []
    ctl.freshSourceReady.connect(ready.append)
    model.set_streams([stale])
    ctl._rows_stored = True
    ctl._streams_loading = True
    ctl.pickWhenFresh(0)
    ctl._clear_streams()
    assert ready == [-1]


async def test_meta_names_the_title_it_belongs_to(qapp: object) -> None:
    # The page shows its content only once metaFor is its own id: a freshly
    # pushed page must never render the previous title's meta.
    ctl = DetailController(FakeGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    await ctl.load("movie", "tt1")
    assert ctl.metaFor == "tt1"
    assert ctl.metaFailedFor == ""

    gate = GatedGetDetail()
    ctl._get_detail = gate  # type: ignore[assignment]
    load = asyncio.ensure_future(ctl.load("movie", "tt2"))
    await gate.entered.wait()
    assert ctl.metaFor == "", "still loading: nothing on screen belongs to tt2 yet"
    gate.gates["tt2"].set()
    await load
    assert ctl.metaFor == "tt2"


async def test_failed_meta_is_reported_for_its_title(qapp: object) -> None:
    ctl = DetailController(FailGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    await ctl.load("movie", "tt1")
    assert ctl.metaFailedFor == "tt1"
    assert ctl.metaFor == ""

    # Retrying clears the verdict before it asks again.
    ctl._get_detail = FakeGetDetail()  # type: ignore[assignment]
    await ctl.load("movie", "tt1")
    assert ctl.metaFailedFor == ""
    assert ctl.metaFor == "tt1"


async def test_a_stream_failure_is_not_a_meta_failure(qapp: object) -> None:
    ctl = DetailController(FakeGetDetail(), FailResolve(), StreamListModel())  # type: ignore[arg-type]
    await ctl.load("movie", "tt1")
    assert ctl.metaFor == "tt1"
    assert ctl.metaFailedFor == ""


async def test_hovering_a_movie_warms_its_meta_and_guesses_its_sources(qapp: object) -> None:
    guessed: list[tuple[str, str]] = []
    asked: list[str] = []

    class Meta(FakeGetDetail):
        async def __call__(self, type, item_id):  # type: ignore[no-untyped-def]
            asked.append(item_id)
            return await super().__call__(type, item_id)

    ctl = DetailController(
        Meta(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        speculate=lambda type, id: guessed.append((type, id)),
    )
    ctl.prefetchTitle("movie", "tt1")
    await _drain_pending_tasks()
    assert guessed == [("movie", "tt1")]
    assert asked == ["tt1"]


async def test_hovering_a_series_guesses_the_episode_it_would_open_on(qapp: object) -> None:
    guessed: list[tuple[str, str]] = []
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        EpisodeListModel(),
        speculate=lambda type, id: guessed.append((type, id)),
    )
    ctl.prefetchTitle("series", "tt1")
    await _drain_pending_tasks()
    # Not the special, not whichever video the addon listed first.
    assert guessed == [("series", "tt1:1:1")]


async def test_a_card_hands_over_what_it_showed(qapp: object) -> None:
    ctl = DetailController(FakeGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    ctl.preview("tt1", "Film", "http://p/1.jpg")
    assert (ctl.previewFor, ctl.previewName, ctl.previewPoster) == ("tt1", "Film", "http://p/1.jpg")
    await ctl.load("movie", "tt2")
    assert ctl.previewFor == "tt1", "a preview names its title; the page compares ids"


async def test_the_next_episode_starts_before_this_one_ends(qapp: object) -> None:
    prefetched: list[tuple[str, str]] = []
    ctl = DetailController(
        SeriesGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        EpisodeListModel(),
        prefetch=lambda type, id: prefetched.append((type, id)),
    )
    await ctl.load("series", "tt1")
    prefetched.clear()

    ctl.prefetch_after("tt1", "tt1:1:1")
    ctl.prefetch_after("tt1", "tt1:1:2")  # season finale: the next season opens
    ctl.prefetch_after("tt1", "tt1:2:1")  # series finale: nothing follows
    ctl.prefetch_after("tt1", "tt1:0:1")  # specials only lead to specials
    ctl.prefetch_after("tt_other", "tt1:1:1")  # not the series on this page
    assert prefetched == [("series", "tt1:1:2"), ("series", "tt1:2:1")]


class _Unavailable:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, type: str, id: str) -> list[Stream]:
        from gravitas.domain.errors import SourcesUnavailable

        self.calls += 1
        if self.calls == 1:
            raise SourcesUnavailable("addon down")
        return [Stream(name="s", title="t", url="http://v", info_hash=None, file_idx=None)]


async def test_an_addon_that_did_not_answer_offers_a_retry(qapp: object) -> None:
    resolve = _Unavailable()
    model = StreamListModel()
    errors: list[str] = []
    ctl = DetailController(FakeGetDetail(), resolve, model)  # type: ignore[arg-type]
    ctl.errorOccurred.connect(errors.append)
    await ctl.load("movie", "tt1")
    assert ctl.sourcesFailed is True
    assert ctl.streamsLoading is False
    assert errors == [], "said on the page, with a retry, not in a passing toast"

    await ctl.retrySources()
    assert ctl.sourcesFailed is False
    assert model.rowCount() == 1
    assert ctl.metaFor == "tt1", "a retry refetches the sources, not the page"


async def test_retry_asks_again_for_the_selected_episode(qapp: object) -> None:
    resolve = _Unavailable()
    model = StreamListModel()
    ctl = DetailController(SeriesGetDetail(), resolve, model, EpisodeListModel())  # type: ignore[arg-type]
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "Two")
    assert ctl.sourcesFailed is True
    await ctl.retrySources()
    assert model.rowCount() == 1
    assert ctl.selectedEpisodeId == "tt1:1:2"


async def test_ranking_runs_off_the_gui_thread(qapp: object, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import threading

    from gravitas.presentation.controllers import detail_controller as module

    threads: list[threading.Thread] = []
    original = module._rank_for_display

    def spy(inputs):  # type: ignore[no-untyped-def]
        threads.append(threading.current_thread())
        return original(inputs)

    monkeypatch.setattr(module, "_rank_for_display", spy)
    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), FakeResolve(), model)  # type: ignore[arg-type]
    await ctl.load("movie", "tt1")
    assert model.rowCount() == 1
    assert threads and all(t is not threading.main_thread() for t in threads)


async def test_an_overtaken_ranking_never_lands(qapp: object, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # tt1's ranking is still on its thread when the viewer opens tt2; when it
    # comes back it must not paint tt1's sources over tt2's page.
    import threading

    from gravitas.presentation.controllers import detail_controller as module

    release = threading.Event()
    original = module._rank_for_display

    def slow_for_tt1(inputs):  # type: ignore[no-untyped-def]
        if any(s.name == "tt1-source" for s in inputs.shown):
            release.wait(5)
        return original(inputs)

    monkeypatch.setattr(module, "_rank_for_display", slow_for_tt1)

    class PerTitle:
        async def __call__(self, type: str, item_id: str) -> list[Stream]:
            return [
                Stream(
                    name=f"{item_id}-source",
                    title="t",
                    url=f"http://v/{item_id}",
                    info_hash=None,
                    file_idx=None,
                )
            ]

    model = StreamListModel()
    ctl = DetailController(FakeGetDetail(), PerTitle(), model)  # type: ignore[arg-type]
    first = asyncio.ensure_future(ctl.load("movie", "tt1"))
    for _ in range(10):
        await asyncio.sleep(0)
    await ctl.load("movie", "tt2")
    release.set()
    await first
    assert [model.stream_at(i).name for i in range(model.rowCount())] == ["tt2-source"]


def _series_meta(item_id: str, episodes_s1: int, name: str = "Show") -> MetaDetail:
    from gravitas.domain.models import Video

    videos = (
        *(
            Video(id=f"{item_id}:1:{n}", title=f"E{n}", season=1, episode=n)
            for n in range(1, episodes_s1 + 1)
        ),
        Video(id=f"{item_id}:2:1", title="S2", season=2, episode=1),
    )
    return MetaDetail(
        id=item_id,
        type="series",
        name=name,
        description=None,
        poster=None,
        background=None,
        videos=videos,
    )


class _SlowMeta:
    """A meta fetch that answers when the test says so."""

    def __init__(self, result: MetaDetail | Exception) -> None:
        self.result = result
        self.release = asyncio.Event()

    async def __call__(self, type: str, item_id: str) -> MetaDetail:
        await self.release.wait()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_a_slow_meta_shows_the_stored_copy_then_the_fresh_one(qapp: object) -> None:
    stored = _series_meta("tt1", 2, name="Show (last week)")
    fresh = _series_meta("tt1", 3, name="Show")
    slow = _SlowMeta(fresh)

    async def stored_meta(type: str, item_id: str) -> MetaDetail:
        return stored

    episodes = EpisodeListModel()
    ctl = DetailController(
        slow,  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        episodes,
        stored_meta=stored_meta,
    )
    load = asyncio.ensure_future(ctl.load("series", "tt1"))
    await _drain_until(lambda: ctl.title == stored.name, turns=1000)
    await asyncio.sleep(DetailController.STORED_META_AFTER_S + 0.05)
    assert ctl.title == "Show (last week)", "the page stands on the stored copy"
    assert ctl.metaFor == "tt1"
    assert episodes.rowCount() == 2

    ctl.selectSeason(1)  # the viewer moves on while the fresh copy is coming
    slow.release.set()
    await load
    assert ctl.title == "Show"
    assert ctl.seasonIndex == 1, "the fresh copy must not reset the season on screen"
    ctl.selectSeason(0)
    assert episodes.rowCount() == 3, "and it brings the new episode"


async def test_a_fast_meta_never_reads_the_stored_copy(qapp: object) -> None:
    read: list[str] = []

    async def stored_meta(type: str, item_id: str) -> MetaDetail | None:
        read.append(item_id)
        return None

    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        stored_meta=stored_meta,
    )
    await ctl.load("movie", "tt1")
    assert read == []
    assert ctl.metaFor == "tt1"


async def test_a_failed_fetch_keeps_the_stored_copy(qapp: object) -> None:
    stored = _series_meta("tt1", 2)

    async def stored_meta(type: str, item_id: str) -> MetaDetail:
        return stored

    slow = _SlowMeta(AddonUnreachable("cinemeta down"))
    errors: list[str] = []
    ctl = DetailController(
        slow,  # type: ignore[arg-type]
        FakeResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        EpisodeListModel(),
        stored_meta=stored_meta,
    )
    ctl.errorOccurred.connect(errors.append)
    load = asyncio.ensure_future(ctl.load("series", "tt1"))
    await asyncio.sleep(DetailController.STORED_META_AFTER_S + 0.05)
    slow.release.set()
    await load
    assert ctl.metaFor == "tt1"
    assert ctl.metaFailedFor == "", "a page from last week beats 'Couldn't load this title'"
    assert errors == []


async def test_the_top_source_of_a_fresh_list_is_warmed(qapp: object) -> None:
    warmed: list[str] = []
    stale = Stream(name="old", title="t", url="http://old", info_hash=None, file_idx=None)

    async def stored(type: str, id: str) -> list[Stream]:
        return [stale]

    ctl = DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        MultiResolve(),  # type: ignore[arg-type]
        StreamListModel(),
        stored=stored,
        warm_link=lambda url, headers: warmed.append(url),
    )
    await ctl.load("movie", "tt1")
    # One link -- each resolve is a debrid lookup -- and never the stored
    # list's, whose links may have expired long ago.
    assert len(warmed) == 1
    assert warmed[0] == ctl._stream_model.stream_at(0).playable_url
    assert "http://old" not in warmed


# --- the player's episode panel ---


async def test_play_episode_fills_the_list_and_says_row_0_is_ready(qapp: object) -> None:
    ctl, stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    ready: list[str] = []
    ctl.episodeReadyToPlay.connect(ready.append)
    await ctl.playEpisode("tt1:1:2", 1, 2, "Two")
    assert ready == ["tt1:1:2"]
    assert stream_model.rowCount() == 1
    # The player takes its identity from here, as the Sources page does.
    context = ctl.mediaContext()
    assert context["videoId"] == "tt1:1:2"
    assert context["label"] == "S1E2 · Two"
    assert ctl.sourceQueue(-1)[0]["url"] == "http://s/v.mkv"


async def test_play_episode_with_nothing_playable_says_why(qapp: object) -> None:
    stream_model = StreamListModel()
    ctl = DetailController(SeriesGetDetail(), NoStreamsResolve(), stream_model, EpisodeListModel())  # type: ignore[arg-type]
    await ctl.load("series", "tt1")
    ready: list[str] = []
    unplayable: list[tuple[str, str]] = []
    ctl.episodeReadyToPlay.connect(ready.append)
    ctl.episodeUnplayable.connect(lambda video, reason: unplayable.append((video, reason)))
    await ctl.playEpisode("tt1:1:1", 1, 1, "One")
    assert ready == []
    assert unplayable == [("tt1:1:1", "No playable sources for this episode.")]


async def test_only_the_latest_play_episode_answers(qapp: object) -> None:
    """Two clicks in a row: the second episode plays, never whichever of the
    two answered last."""

    class SlowFirst:
        async def __call__(self, type, item_id):
            if item_id == "tt1:1:1":
                await asyncio.sleep(0.05)
            return [
                Stream(
                    name="1080p",
                    title=item_id,
                    url=f"http://s/{item_id}.mkv",
                    info_hash=None,
                    file_idx=None,
                )
            ]

    ctl = DetailController(SeriesGetDetail(), SlowFirst(), StreamListModel(), EpisodeListModel())  # type: ignore[arg-type]
    await ctl.load("series", "tt1")
    ready: list[str] = []
    ctl.episodeReadyToPlay.connect(ready.append)
    await asyncio.gather(
        ctl.playEpisode("tt1:1:1", 1, 1, "One"), ctl.playEpisode("tt1:1:2", 1, 2, "Two")
    )
    assert ready == ["tt1:1:2"]
    assert ctl.sourceQueue(-1)[0]["url"] == "http://s/tt1:1:2.mkv"


async def test_the_panel_finds_the_playing_episode(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    assert ctl.episodeInfo("tt1:1:2") == {
        "title": "Two",
        "overview": "",
        "thumbnail": "http://t/2.jpg",
        "season": 1,
        "episode": 2,
        "label": "S1E2 · Two",
    }
    assert ctl.episodeInfo("tt9:1:1") == {}
    # Seasons are listed 1, 2, Specials.
    assert ctl.seasonIndexOf("tt1:2:1") == 1
    assert ctl.seasonIndexOf("tt1:0:1") == 2
    assert ctl.seasonIndexOf("nope") == -1
    # Rows are of the season now listed (season 1: One, Two).
    assert ctl.episodeRowOf("tt1:1:2") == 1
    assert ctl.episodeRowOf("tt1:2:1") == -1


async def test_next_episode_follows_watching_order(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    within = ctl.nextEpisode("tt1:1:1")
    assert within["videoId"] == "tt1:1:2"
    assert within["label"] == "S1E2 · Two"
    assert within["newSeason"] is False
    # The last episode of a season leads into the next season.
    across = ctl.nextEpisode("tt1:1:2")
    assert across["videoId"] == "tt1:2:1"
    assert across["newSeason"] is True
    assert across["season"] == 2
    # A finale has nothing after it, and a regular episode never leads into
    # the specials; a special does not lead back into the regular seasons.
    assert ctl.nextEpisode("tt1:2:1") == {}
    assert ctl.nextEpisode("tt1:0:1") == {}
    assert ctl.nextEpisode("not-an-episode") == {}


async def test_next_episode_skips_a_gap_in_the_numbering(qapp: object) -> None:
    class Gapped:
        async def __call__(self, type, item_id):
            return MetaDetail(
                id="tt1",
                type="series",
                name="Show",
                description="d",
                poster=None,
                background=None,
                videos=(
                    Video(id="tt1:1:1", title="One", season=1, episode=1),
                    Video(id="tt1:1:3", title="Three", season=1, episode=3),
                    Video(id="tt1:3:1", title="Later", season=3, episode=1),
                ),
            )

    ctl = DetailController(Gapped(), FakeResolve(), StreamListModel(), EpisodeListModel())  # type: ignore[arg-type]
    await ctl.load("series", "tt1")
    assert ctl.nextEpisode("tt1:1:1")["videoId"] == "tt1:1:3"
    # A missing season is skipped too.
    assert ctl.nextEpisode("tt1:1:3")["videoId"] == "tt1:3:1"
