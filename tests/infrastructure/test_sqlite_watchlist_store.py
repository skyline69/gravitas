from pathlib import Path

from pytest import MonkeyPatch

from gravitas.domain.models import WatchlistEntry
from gravitas.infrastructure.paths import data_dir
from gravitas.infrastructure.watchlist.sqlite_store import (
    SqliteWatchlistStore,
    default_watchlist_path,
)


def entry(media_id: str = "tt1", **kw: object) -> WatchlistEntry:
    base: dict[str, object] = {
        "media_id": media_id,
        "type": "movie",
        "name": "The Movie",
        "poster": "http://p/1.jpg",
        "year": "2020",
        "added_at": 100,
    }
    base.update(kw)
    return WatchlistEntry(**base)  # type: ignore[arg-type]


def test_round_trip(tmp_path: Path) -> None:
    store = SqliteWatchlistStore(tmp_path / "watchlist.db")
    store.save(entry())
    assert SqliteWatchlistStore(tmp_path / "watchlist.db").load_all() == [entry()]


def test_save_upserts_rather_than_duplicates(tmp_path: Path) -> None:
    store = SqliteWatchlistStore(tmp_path / "watchlist.db")
    store.save(entry(name="Old"))
    store.save(entry(name="New"))
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].name == "New"


def test_poster_and_year_none_survive(tmp_path: Path) -> None:
    store = SqliteWatchlistStore(tmp_path / "watchlist.db")
    store.save(entry(poster=None, year=None))
    loaded = store.load_all()[0]
    assert loaded.poster is None
    assert loaded.year is None


def test_delete_leaves_others(tmp_path: Path) -> None:
    store = SqliteWatchlistStore(tmp_path / "watchlist.db")
    store.save(entry("tt1"))
    store.save(entry("tt2", type="series"))
    store.delete("tt1")
    assert [e.media_id for e in store.load_all()] == ["tt2"]


def test_clear(tmp_path: Path) -> None:
    store = SqliteWatchlistStore(tmp_path / "watchlist.db")
    store.save(entry())
    store.clear()
    assert store.load_all() == []


def test_unwritable_database_degrades_quietly(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    import sqlite3

    def _raise(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise OSError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", _raise)
    store = SqliteWatchlistStore(tmp_path / "watchlist.db")
    assert store.load_all() == []
    store.save(entry())
    store.delete("tt1")
    store.clear()
    assert store.load_all() == []


def test_corrupt_file_loads_empty_and_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "watchlist.db"
    path.write_text("this is not a database", encoding="utf-8")
    store = SqliteWatchlistStore(path)
    assert store.load_all() == []
    store.save(entry())
    store.delete("tt1")
    store.clear()


def test_default_path_lives_in_the_data_dir() -> None:
    assert default_watchlist_path() == data_dir() / "watchlist.db"
