from gravitas.application.addon_repository import AddonRepository, CatalogOption
from gravitas.application.browse_board import BoardPage, BrowseBoard
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)


def _item(i: int) -> MediaItem:
    return MediaItem(id=f"tt{i}", type="movie", name=str(i), poster=None)


class FakeSource:
    def __init__(self, page: list[MediaItem]) -> None:
        self.page = page
        self.calls: list[tuple[str, str | None, int]] = []

    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id="a",
            name="Addon A",
            version="1",
            resources=("catalog",),
            types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top", genres=("Action",)),),
            base_url=url,
        )

    async def fetch_catalog(
        self, manifest: AddonManifest, ref: CatalogRef, *, genre: str | None = None, skip: int = 0
    ) -> list[MediaItem]:
        self.calls.append((ref.id, genre, skip))
        return self.page

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        raise NotImplementedError

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        raise NotImplementedError


async def _installed_repo(page: list[MediaItem]) -> AddonRepository:
    repo = AddonRepository(FakeSource(page))
    await repo.install("https://a/manifest.json")
    return repo


async def test_browse_board_full_page_has_more() -> None:
    repo = await _installed_repo([_item(i) for i in range(100)])
    page = await BrowseBoard(repo)("a", "movie", "top", genre="Action", skip=0)
    assert isinstance(page, BoardPage)
    assert len(page.items) == 100
    assert page.has_more is True


async def test_browse_board_short_page_no_more() -> None:
    repo = await _installed_repo([_item(i) for i in range(10)])
    page = await BrowseBoard(repo)("a", "movie", "top")
    assert page.has_more is False


async def test_browse_board_unknown_catalog_is_empty() -> None:
    repo = await _installed_repo([_item(0)])
    page = await BrowseBoard(repo)("a", "movie", "nope")
    assert page.items == []
    assert page.has_more is False


async def test_resolve_catalog_and_options() -> None:
    repo = await _installed_repo([_item(0)])
    resolved = repo.resolve_catalog("a", "movie", "top")
    assert resolved is not None and resolved[1].id == "top"
    options = repo.catalog_options()
    assert options == [
        CatalogOption(addon_id="a", type="movie", catalog_id="top", label="Top", genres=("Action",))
    ]
