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
