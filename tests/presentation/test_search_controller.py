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


async def test_clear_invalidates_inflight(qapp: object) -> None:
    items = [MediaItem(id="tt1", type="movie", name="A", poster=None)]
    c, model = _build(items, items[0])
    token = c._req  # type: ignore[attr-defined]
    c.clear()  # bumps _req, invalidating the in-flight token
    assert c._req != token  # type: ignore[attr-defined]
    await c._perform_with_id("q", token)  # in-flight completes with the pre-clear token
    assert model.rowCount() == 0


async def test_commit_to_page_snapshots(qapp: object) -> None:
    items = [MediaItem(id="tt1", type="movie", name="A", poster=None)]
    page = SearchResultsModel()
    c = SearchController(_FakeSearch(items), _FakeResolve(items[0]), SearchResultsModel(), page)
    await c._perform("matrix")
    assert page.rowCount() == 0  # not committed yet
    c.commitToPage()
    assert page.rowCount() == 1


class _FakeStream:
    def __init__(self, batches: list[list[MediaItem]]) -> None:
        self._batches = batches
        self.calls: list[str] = []

    async def __call__(self, query: str):  # async generator
        self.calls.append(query)
        for batch in self._batches:
            yield batch


async def test_streaming_accumulates_batches(qapp: object) -> None:
    a = MediaItem(id="tt1", type="movie", name="A", poster=None)
    b = MediaItem(id="tt2", type="movie", name="B", poster=None)
    model = SearchResultsModel()
    stream = _FakeStream([[a], [b]])
    c = SearchController(_FakeSearch([]), _FakeResolve(a), model, None, stream)
    await c._perform("matrix")
    assert stream.calls == ["matrix"]
    assert model.rowCount() == 2  # accumulated across both batches


async def test_cache_hit_skips_second_search(qapp: object) -> None:
    a = MediaItem(id="tt1", type="movie", name="A", poster=None)
    model = SearchResultsModel()
    stream = _FakeStream([[a]])
    c = SearchController(_FakeSearch([]), _FakeResolve(a), model, None, stream)
    await c._perform("matrix")
    await c._perform("matrix")  # second call served from cache
    assert stream.calls == ["matrix"]  # stream invoked only once
    assert model.rowCount() == 1
