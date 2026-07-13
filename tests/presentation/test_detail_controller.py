import asyncio

from gravitas.domain.errors import AddonUnreachable, NoStreams
from gravitas.domain.models import MetaDetail, Stream
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
