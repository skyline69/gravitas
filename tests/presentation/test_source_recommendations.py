"""The Recommended marks as the Sources page receives them."""

import time

from gravitas.application.compatibility import IncompatibleSources
from gravitas.application.connection_speed import ConnectionSpeed
from gravitas.application.playback_capability import PlaybackCapability
from gravitas.application.recommendation import SourceRecommendations
from gravitas.domain.models import ConnectionSample, MetaDetail, Stream
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.models.stream_list_model import StreamListModel


def _stream(name: str) -> Stream:
    return Stream(name=name, title="", url="http://h/v.mkv", info_hash=None, file_idx=None)


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
            runtime="102 min",
        )


class FakeResolve:
    def __init__(self, streams: list[Stream]) -> None:
        self._streams = streams

    async def __call__(self, type, item_id):
        return list(self._streams)


def _connection(kbps: int | None) -> ConnectionSpeed:
    connection = ConnectionSpeed(lambda: "wifi")
    connection.enabled = False  # the sort stays off; only the estimate is read
    if kbps is not None:
        connection.samples = (ConnectionSample(kbps=kbps, at=int(time.time()), bucket="wifi"),)
    return connection


def _rows(model: StreamListModel) -> list[tuple[str, bool, str]]:
    return [
        (
            model.data(model.index(row, 0), StreamListModel.NameRole),
            model.data(model.index(row, 0), StreamListModel.RecommendedRole),
            model.data(model.index(row, 0), StreamListModel.SectionRole),
        )
        for row in range(model.rowCount())
    ]


def _controller(
    streams: list[Stream],
    *,
    model: StreamListModel,
    kbps: int | None = 100_000,
    display_height: int = 2160,
    recommendations: SourceRecommendations | None = None,
    capability: PlaybackCapability | None = None,
    incompatible: IncompatibleSources | None = None,
) -> DetailController:
    return DetailController(
        FakeGetDetail(),  # type: ignore[arg-type]
        FakeResolve(streams),  # type: ignore[arg-type]
        model,
        connection=_connection(kbps),
        display_height=lambda: display_height,
        recommendations=(
            recommendations if recommendations is not None else SourceRecommendations()
        ),
        capability=capability,
        incompatible=incompatible,
    )


async def test_the_picks_are_marked_and_lifted_to_the_top():
    model = StreamListModel()
    streams = [
        _stream("720p ⟨Web-dl⟩ · 3 Mbps"),
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps"),
    ]
    await _controller(streams, model=model).load("movie", "tt1")

    rows = _rows(model)
    # Best tier first, and every marked row is above the rest.
    assert [name for name, _, _ in rows][:3] == [
        "4K ⟨Web-dl⟩ · 24 Mbps",
        "1080p ⟨Web-dl⟩ · 8 Mbps",
        "720p ⟨Web-dl⟩ · 3 Mbps",
    ]
    assert [marked for _, marked, _ in rows] == [True, True, True]


async def test_the_reason_travels_with_the_row_it_justifies():
    model = StreamListModel()
    streams = [
        _stream("4K ⟨Web-dl⟩ · 22 Mbps"),
        _stream("4K ⚡ ⟨Web-dl⟩ · 24 Mbps"),
    ]
    await _controller(streams, model=model).load("movie", "tt1")

    top = model.index(0, 0)
    assert model.data(top, StreamListModel.NameRole) == "4K ⚡ ⟨Web-dl⟩ · 24 Mbps"
    reason = model.data(top, StreamListModel.ReasonRole)
    assert "4K" in reason and "starts instantly" in reason
    # The unmarked row carries no sentence at all.
    assert model.data(model.index(1, 0), StreamListModel.ReasonRole) == ""


async def test_unmarked_rows_keep_the_order_the_list_already_had():
    model = StreamListModel()
    streams = [
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps · A"),
        _stream("1080p ⟨Web-dl⟩ · 7 Mbps · B"),
        _stream("1080p ⟨Web-dl⟩ · 6 Mbps · C"),
    ]
    # A 1080p panel collapses these into one tier; A is the pick, B is not
    # cheap enough to be the lighter alternative, so B and C keep their order.
    await _controller(streams, model=model, display_height=1080).load("movie", "tt1")
    assert [name for name, _, _ in _rows(model)] == [
        "1080p ⟨Web-dl⟩ · 8 Mbps · A",
        "1080p ⟨Web-dl⟩ · 7 Mbps · B",
        "1080p ⟨Web-dl⟩ · 6 Mbps · C",
    ]


async def test_sections_appear_only_once_something_is_marked():
    model = StreamListModel()
    marked = [_stream("1080p ⟨Web-dl⟩ · 8 Mbps"), _stream("720p ⟨Web-dl⟩ · 3 Mbps")]
    await _controller(marked, model=model).load("movie", "tt1")
    assert [section for _, _, section in _rows(model)] == ["Recommended", "Recommended"]

    unmarkable = StreamListModel()
    await _controller([_stream("Silo S01E01 ⟨Web-dl⟩ ⛨ [TB] STorz")], model=unmarkable).load(
        "movie", "tt1"
    )
    assert [section for _, _, section in _rows(unmarkable)] == [""]


async def test_the_rest_of_the_list_is_filed_under_all_sources():
    model = StreamListModel()
    streams = [
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps"),
        _stream("1080p ⟨Web-dl⟩ · 7.5 Mbps"),
    ]
    await _controller(streams, model=model, display_height=1080).load("movie", "tt1")
    assert [section for _, _, section in _rows(model)] == ["Recommended", "All sources"]


async def test_the_setting_off_leaves_the_list_exactly_as_it_was():
    model = StreamListModel()
    off = SourceRecommendations()
    off.enabled = False
    streams = [
        _stream("720p ⟨Web-dl⟩ · 3 Mbps"),
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
    ]
    await _controller(streams, model=model, recommendations=off).load("movie", "tt1")

    rows = _rows(model)
    assert [name for name, _, _ in rows] == [
        "720p ⟨Web-dl⟩ · 3 Mbps",
        "4K ⟨Web-dl⟩ · 24 Mbps",
    ]
    assert not any(marked for _, marked, _ in rows)


async def test_what_this_machine_drops_frames_on_is_not_marked():
    model = StreamListModel()
    capability = PlaybackCapability()
    capability.strained = ("av1:2160",)
    streams = [
        _stream("4K ▣ AV1 ⟨Web-dl⟩ · 24 Mbps"),
        _stream("4K ▣ HEVC · HDR10 ⟨Web-dl⟩ · 20 Mbps"),
    ]
    await _controller(streams, model=model, capability=capability).load("movie", "tt1")

    rows = _rows(model)
    assert rows[0][0] == "4K ▣ HEVC · HDR10 ⟨Web-dl⟩ · 20 Mbps"
    assert rows[0][1] is True
    assert rows[1][1] is False


async def test_revealed_incompatible_sources_are_never_marked():
    model = StreamListModel()
    incompatible = IncompatibleSources()
    incompatible.enabled = True
    streams = [
        _stream("4K ▣ HEVC ✦ DV ⟨Web-dl⟩ · 24 Mbps"),  # profile 5 shape: hidden
        _stream("1080p ⟨Web-dl⟩ · 8 Mbps"),
    ]
    controller = _controller(streams, model=model, incompatible=incompatible)
    await controller.load("movie", "tt1")
    assert model.rowCount() == 1

    await controller.showHiddenSources()
    rows = _rows(model)
    assert [marked for _, marked, _ in rows] == [True, False]
    assert rows[1][0] == "4K ▣ HEVC ✦ DV ⟨Web-dl⟩ · 24 Mbps"


async def test_nothing_measured_still_marks_the_best_row_per_quality():
    model = StreamListModel()
    streams = [
        _stream("4K ⟨Remux⟩ · 80 Mbps"),
        _stream("4K ⟨Web-dl⟩ · 24 Mbps"),
    ]
    await _controller(streams, model=model, kbps=None).load("movie", "tt1")

    rows = _rows(model)
    # The remux is over the unmeasured ceiling, so it is listed but not marked.
    assert rows[0][0] == "4K ⟨Web-dl⟩ · 24 Mbps"
    assert [marked for _, marked, _ in rows] == [True, False]
