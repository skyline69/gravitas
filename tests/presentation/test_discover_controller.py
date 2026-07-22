from gravitas.application.addon_repository import CatalogOption
from gravitas.application.browse_board import BoardPage
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import MediaItem
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.models.poster_grid_model import PosterGridModel


def _items(n: int, offset: int = 0) -> list[MediaItem]:
    return [
        MediaItem(id=f"tt{offset + i}", type="movie", name=str(offset + i), poster=None)
        for i in range(n)
    ]


class FakeRepo:
    def catalog_options(self) -> list[CatalogOption]:
        return [
            CatalogOption(
                addon_id="a", type="movie", catalog_id="top", label="Top", genres=("Action",)
            ),
            CatalogOption(addon_id="a", type="series", catalog_id="pop", label="Pop", genres=()),
        ]


class FakeBrowse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str | None, int]] = []
        self.has_more = True

    async def __call__(self, addon_id, type, catalog_id, *, genre=None, skip=0):
        self.calls.append((addon_id, type, catalog_id, genre, skip))
        return BoardPage(items=_items(100, offset=skip), has_more=self.has_more)


async def test_open_loads_first_page_and_options(qapp: object) -> None:
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    assert model.rowCount() == 100
    assert list(ctl.typeOptions) == ["movie", "series"]
    assert list(ctl.catalogOptions) == ["Top"]
    assert list(ctl.genreOptions) == ["All genres", "Action"]
    assert browse.calls[-1] == ("a", "movie", "top", None, 0)


async def test_select_genre_reloads_with_genre(qapp: object) -> None:
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    await ctl.selectGenre(1)  # "Action"
    assert browse.calls[-1] == ("a", "movie", "top", "Action", 0)
    assert model.rowCount() == 100  # reset, not appended


async def test_load_more_appends_and_respects_has_more(qapp: object) -> None:
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    await ctl.loadMore()
    assert browse.calls[-1] == ("a", "movie", "top", None, 100)
    assert model.rowCount() == 200
    browse.has_more = False
    await ctl.loadMore()  # fetches skip=200, has_more now False
    assert model.rowCount() == 300
    calls_before = len(browse.calls)
    await ctl.loadMore()  # no has_more -> no fetch
    assert len(browse.calls) == calls_before


async def test_load_more_stops_when_addon_repeats_page(qapp: object) -> None:
    # Addons that ignore `skip` return the same items forever; pagination must
    # stop once a page appends nothing new instead of refetching endlessly.
    class RepeatingBrowse(FakeBrowse):
        async def __call__(self, addon_id, type, catalog_id, *, genre=None, skip=0):
            self.calls.append((addon_id, type, catalog_id, genre, skip))
            return BoardPage(items=_items(100, offset=0), has_more=True)

    model = PosterGridModel()
    browse = RepeatingBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    await ctl.loadMore()  # same page again -> 0 appended
    assert model.rowCount() == 100  # no duplicates
    calls_before = len(browse.calls)
    await ctl.loadMore()  # pagination now off
    assert len(browse.calls) == calls_before


async def test_error_emits_and_clears_loading(qapp: object) -> None:
    class Boom:
        async def __call__(self, *a, **k):
            raise AddonUnreachable("boom")

    model = PosterGridModel()
    ctl = DiscoverController(Boom(), FakeRepo(), model)  # type: ignore[arg-type]
    errors: list[str] = []
    loading: list[bool] = []
    ctl.errorOccurred.connect(errors.append)
    ctl.loadingChanged.connect(loading.append)
    await ctl.open("a", "movie", "top")
    assert errors == ["boom"]
    assert loading == [True, False]


async def test_page_replacement_emits_board_replaced(qapp: object) -> None:
    """Every non-append load (open, type/catalog/genre switch) must announce
    itself: the grid keeps its scroll offset across a model reset, so without
    this signal a switch left the view parked mid-list — often past the new
    content's end, where a near-empty band of delegates is all that renders."""
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    fired: list[None] = []
    ctl.boardReplaced.connect(lambda: fired.append(None))

    await ctl.open("a", "movie", "top")
    assert len(fired) == 1
    await ctl.selectType(1)
    assert len(fired) == 2
    await ctl.selectGenre(0)
    assert len(fired) == 3

    # Pagination extends the same board — the scroll position must survive.
    await ctl.selectType(0)
    assert len(fired) == 4
    await ctl.loadMore()
    assert len(fired) == 4


async def test_board_replaced_fires_even_for_an_empty_board(qapp: object) -> None:
    class NoCatalogRepo:
        def catalog_options(self) -> list[CatalogOption]:
            return []

    model = PosterGridModel()
    ctl = DiscoverController(FakeBrowse(), NoCatalogRepo(), model)  # type: ignore[arg-type]
    fired: list[None] = []
    ctl.boardReplaced.connect(lambda: fired.append(None))
    await ctl.open("a", "movie", "top")
    assert len(fired) == 1
