from gravitas.application.watchlist import WatchlistRepository
from gravitas.domain.models import WatchlistEntry
from gravitas.presentation.controllers.watchlist_controller import WatchlistController
from gravitas.presentation.models.poster_grid_model import PosterGridModel


class FakeStore:
    def __init__(self, entries: list[WatchlistEntry] | None = None) -> None:
        self.entries = {e.media_id: e for e in (entries or [])}

    def load_all(self) -> list[WatchlistEntry]:
        return list(self.entries.values())

    def save(self, entry: WatchlistEntry) -> None:
        self.entries[entry.media_id] = entry

    def delete(self, media_id: str) -> None:
        self.entries.pop(media_id, None)

    def clear(self) -> None:
        self.entries.clear()


CONTEXT = {
    "mediaId": "tt1",
    "type": "movie",
    "name": "The Movie",
    "poster": "http://p/1.jpg",
    "year": "2020",
}


def make_controller(
    entries: list[WatchlistEntry] | None = None,
) -> tuple[WatchlistController, PosterGridModel, PosterGridModel]:
    movies = PosterGridModel()
    series = PosterGridModel()
    repo = WatchlistRepository(FakeStore(entries), clock=lambda: 500)
    return WatchlistController(repo, movies, series), movies, series


def test_models_primed_from_persisted_entries(qapp: object) -> None:
    saved = WatchlistEntry(
        media_id="tt1", type="movie", name="The Movie", poster=None, year=None, added_at=100
    )
    _controller, movies, series = make_controller([saved])
    assert movies.rowCount() == 1
    assert movies.item_at(0).id == "tt1"
    assert series.rowCount() == 0


def test_toggle_adds_then_removes(qapp: object) -> None:
    controller, movies, _series = make_controller()
    assert controller.contains("tt1") is False

    controller.toggle(CONTEXT)
    assert controller.contains("tt1") is True
    assert movies.rowCount() == 1
    item = movies.item_at(0)
    assert (item.name, item.poster, item.year) == ("The Movie", "http://p/1.jpg", "2020")

    controller.toggle(CONTEXT)
    assert controller.contains("tt1") is False
    assert movies.rowCount() == 0


def test_toggle_bumps_revision_and_signals(qapp: object) -> None:
    controller, _movies, _series = make_controller()
    fired: list[None] = []
    controller.watchlistChanged.connect(lambda: fired.append(None))
    before = controller.revision
    controller.toggle(CONTEXT)
    assert controller.revision == before + 1
    assert fired == [None]


def test_toggle_without_media_id_is_ignored(qapp: object) -> None:
    controller, movies, series = make_controller()
    before = controller.revision
    controller.toggle({"name": "nameless"})
    assert controller.revision == before
    assert movies.rowCount() == 0
    assert series.rowCount() == 0


def test_empty_poster_and_year_stored_as_none(qapp: object) -> None:
    controller, movies, _series = make_controller()
    controller.toggle({**CONTEXT, "poster": "", "year": ""})
    item = movies.item_at(0)
    assert item.poster is None
    assert item.year is None


def test_remove_slot(qapp: object) -> None:
    controller, movies, _series = make_controller()
    controller.toggle(CONTEXT)
    controller.remove("tt1")
    assert controller.contains("tt1") is False
    assert movies.rowCount() == 0


def test_entries_split_by_type(qapp: object) -> None:
    controller, movies, series = make_controller()
    controller.toggle(CONTEXT)
    controller.toggle({**CONTEXT, "mediaId": "tt9", "type": "series"})
    assert [movies.item_at(i).id for i in range(movies.rowCount())] == ["tt1"]
    assert [series.item_at(i).id for i in range(series.rowCount())] == ["tt9"]
    assert series.item_at(0).type == "series"
