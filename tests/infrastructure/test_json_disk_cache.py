from pathlib import Path

from pytest import MonkeyPatch

from gravitas.infrastructure.cache.json_disk_cache import JsonDiskCache


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_roundtrip_reports_age(tmp_path: Path) -> None:
    clock = _Clock()
    cache = JsonDiskCache(tmp_path / "cache.db", clock=clock)
    cache.put("https://a/catalog.json", {"metas": [1, 2]})
    clock.now += 90
    hit = cache.get("https://a/catalog.json")
    assert hit is not None
    data, age = hit
    assert data == {"metas": [1, 2]}
    assert age == 90


def test_survives_a_reopen(tmp_path: Path) -> None:
    clock = _Clock()
    JsonDiskCache(tmp_path / "cache.db", clock=clock).put("u", {"k": "v"})
    reopened = JsonDiskCache(tmp_path / "cache.db", clock=clock)
    hit = reopened.get("u")
    assert hit is not None and hit[0] == {"k": "v"}


def test_missing_url_is_none(tmp_path: Path) -> None:
    assert JsonDiskCache(tmp_path / "cache.db").get("nope") is None


def test_put_overwrites_and_restamps(tmp_path: Path) -> None:
    clock = _Clock()
    cache = JsonDiskCache(tmp_path / "cache.db", clock=clock)
    cache.put("u", {"v": 1})
    clock.now += 500
    cache.put("u", {"v": 2})
    hit = cache.get("u")
    assert hit is not None
    assert hit[0] == {"v": 2}
    assert hit[1] == 0


def test_prune_drops_only_old_entries(tmp_path: Path) -> None:
    clock = _Clock()
    cache = JsonDiskCache(tmp_path / "cache.db", clock=clock)
    cache.put("old", {"v": 1})
    clock.now += 100
    cache.put("new", {"v": 2})
    assert cache.prune(max_age=50) == 1
    assert cache.get("old") is None
    assert cache.get("new") is not None


def test_unwritable_database_degrades_quietly(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    import sqlite3

    def _raise(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise OSError("unable to open database file")

    monkeypatch.setattr(sqlite3, "connect", _raise)
    cache = JsonDiskCache(tmp_path / "cache.db")
    cache.put("u", {"v": 1})  # must not raise
    assert cache.get("u") is None
    assert cache.prune() == 0


def test_unserializable_payload_is_dropped(tmp_path: Path) -> None:
    cache = JsonDiskCache(tmp_path / "cache.db")
    cache.put("u", {"bad": object()})  # type: ignore[dict-item]  # must not raise
    assert cache.get("u") is None
