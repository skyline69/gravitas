from gravitas.application.watchlist import WatchlistRepository
from gravitas.domain.models import WatchlistEntry


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


def entry(media_id: str = "tt1", added_at: int = 100, **kw: object) -> WatchlistEntry:
    base: dict[str, object] = {
        "media_id": media_id,
        "type": "movie",
        "name": "The Movie",
        "poster": "http://p/1.jpg",
        "year": "2020",
        "added_at": added_at,
    }
    base.update(kw)
    return WatchlistEntry(**base)  # type: ignore[arg-type]


def make_repo(
    entries: list[WatchlistEntry] | None = None, now: int = 500
) -> tuple[WatchlistRepository, FakeStore]:
    store = FakeStore(entries)
    return WatchlistRepository(store, clock=lambda: now), store


def test_starts_from_persisted_entries() -> None:
    repo, _ = make_repo([entry("tt1"), entry("tt2", added_at=200)])
    assert repo.contains("tt1") is True
    assert repo.contains("tt2") is True
    assert repo.contains("tt3") is False


def test_add_persists_and_indexes() -> None:
    repo, store = make_repo()
    repo.add(media_id="tt1", type="movie", name="The Movie", poster=None, year=None)
    assert repo.contains("tt1") is True
    assert store.entries["tt1"].added_at == 500


def test_add_without_media_id_is_ignored() -> None:
    repo, store = make_repo()
    repo.add(media_id="", type="movie", name="x", poster=None, year=None)
    assert repo.entries() == []
    assert store.entries == {}


def test_entries_newest_first() -> None:
    repo, _ = make_repo([entry("tt1", added_at=100), entry("tt2", added_at=300)])
    assert [e.media_id for e in repo.entries()] == ["tt2", "tt1"]


def test_re_add_updates_metadata_but_keeps_the_original_slot() -> None:
    repo, store = make_repo([entry("tt1", added_at=100)], now=999)
    repo.add(media_id="tt1", type="movie", name="New Name", poster="http://p/2.jpg", year="2021")
    saved = store.entries["tt1"]
    assert saved.name == "New Name"
    # Refreshing the poster must not silently bump the title to the top.
    assert saved.added_at == 100


def test_remove_deletes_and_unindexes() -> None:
    repo, store = make_repo([entry("tt1")])
    repo.remove("tt1")
    assert repo.contains("tt1") is False
    assert store.entries == {}


def test_remove_of_absent_id_does_not_touch_the_store() -> None:
    class ExplodingStore(FakeStore):
        def delete(self, media_id: str) -> None:
            raise AssertionError("delete must not be called for an absent id")

    store = ExplodingStore()
    repo = WatchlistRepository(store)
    repo.remove("tt-missing")  # must not raise
