from pathlib import Path

from pytest import MonkeyPatch

from gravitas.domain.models import PlaybackProgress
from gravitas.infrastructure.progress.sqlite_store import (
    SqliteProgressStore,
    default_progress_path,
)


def entry(media_id: str = "tt1", video_id: str = "", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "The Movie",
        "poster": "http://p/1.jpg",
        "label": "",
        "position": 120.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def test_round_trip(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry())
    assert SqliteProgressStore(tmp_path / "progress.db").load_all() == [entry()]


def test_save_upserts_rather_than_duplicates(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry(position=120.0))
    store.save(entry(position=300.0, updated_at=200))
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].position == 300.0


def test_poster_none_survives(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry(poster=None))
    assert store.load_all()[0].poster is None


def test_watched_flag_survives(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry(watched=True, position=0.0))
    assert store.load_all()[0].watched is True


def test_delete_one_video_leaves_siblings(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry("tt9", "tt9:1:1", type="series"))
    store.save(entry("tt9", "tt9:1:2", type="series"))
    store.delete("tt9", "tt9:1:1")
    assert [e.video_id for e in store.load_all()] == ["tt9:1:2"]


def test_delete_whole_media(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry("tt9", "tt9:1:1", type="series"))
    store.save(entry("tt9", "tt9:1:2", type="series"))
    store.save(entry("tt1", ""))
    store.delete("tt9")
    assert [e.media_id for e in store.load_all()] == ["tt1"]


def test_clear(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry())
    store.clear()
    assert store.load_all() == []


def test_missing_parent_dir_is_auto_provisioned_and_loads_empty(tmp_path: Path) -> None:
    """A "missing" path just gets mkdir -p'd into a fresh valid database by
    _connect(); this only pins that a brand-new table has no rows to load."""
    assert SqliteProgressStore(tmp_path / "nope" / "progress.db").load_all() == []


def test_unwritable_database_degrades_quietly(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Force _connect() into its OSError branch (e.g. an unwritable data
    directory) and confirm every operation degrades instead of raising."""
    import sqlite3

    def _raise(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise OSError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", _raise)
    store = SqliteProgressStore(tmp_path / "progress.db")
    assert store.load_all() == []
    # Every mutation must degrade quietly too.
    store.save(entry())
    store.delete("tt1")
    store.clear()
    assert store.load_all() == []


def test_corrupt_file_loads_empty_and_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "progress.db"
    path.write_text("this is not a database", encoding="utf-8")
    store = SqliteProgressStore(path)
    assert store.load_all() == []
    # Every mutation must degrade quietly too.
    store.save(entry())
    store.delete("tt1")
    store.clear()


def test_schema_version_recorded(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "progress.db"
    SqliteProgressStore(path).save(entry())
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_default_path_follows_xdg_data_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_progress_path() == tmp_path / "gravitas" / "progress.db"


def test_delete_many_removes_exactly_those_rows(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry("tt1", ""))
    store.save(entry("tt9", "tt9:1:1", type="series"))
    store.save(entry("tt9", "tt9:1:2", type="series"))
    store.delete_many([("tt1", ""), ("tt9", "tt9:1:2")])
    left = [(e.media_id, e.video_id) for e in store.load_all()]
    assert left == [("tt9", "tt9:1:1")]


def test_delete_many_with_no_keys_is_a_noop(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry())
    store.delete_many([])
    assert len(store.load_all()) == 1


def test_delete_many_on_a_broken_database_degrades_quietly(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path = tmp_path / "progress.db"
    path.write_text("this is not a database", encoding="utf-8")
    SqliteProgressStore(path).delete_many([("tt1", "")])
