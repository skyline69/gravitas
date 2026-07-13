from gravitas.domain.models import MediaItem
from gravitas.presentation.controllers.search_controller import SearchController
from gravitas.presentation.models.search_results_model import SearchResultsModel


class _FakeSearch:
    def __init__(self, items: list[MediaItem]) -> None:
        self._items = items
        self.calls: list[str] = []

    async def __call__(self, query: str) -> list[MediaItem]:
        self.calls.append(query)
        return self._items


class _FakeResolve:
    def __init__(self, item: MediaItem) -> None:
        self._item = item
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, source: str, external_id: str) -> MediaItem:
        self.calls.append((source, external_id))
        return self._item


def _build(search_items, resolve_item):
    model = SearchResultsModel()
    c = SearchController(_FakeSearch(search_items), _FakeResolve(resolve_item), model)
    return c, model


async def test_perform_text_query_runs_search(qapp: object) -> None:
    items = [MediaItem(id="tt1", type="movie", name="A", poster=None)]
    c, model = _build(items, items[0])
    await c._perform("matrix")
    assert model.rowCount() == 1
    assert c._search.calls == ["matrix"]  # type: ignore[attr-defined]


async def test_perform_link_runs_resolve(qapp: object) -> None:
    resolved = MediaItem(id="tt0133093", type="movie", name="Matrix", poster=None)
    c, model = _build([], resolved)
    await c._perform("https://www.imdb.com/title/tt0133093/")
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), SearchResultsModel.IdRole) == "tt0133093"
    assert c._resolve.calls == [("imdb", "tt0133093")]  # type: ignore[attr-defined]


async def test_stale_request_does_not_clobber(qapp: object) -> None:
    items = [MediaItem(id="tt1", type="movie", name="A", poster=None)]
    c, model = _build(items, items[0])
    # Simulate an in-flight older request completing after a newer one bumped _req.
    c._req = 5  # type: ignore[attr-defined]
    await c._perform_with_id("old", 4)  # older id -> ignored
    assert model.rowCount() == 0
