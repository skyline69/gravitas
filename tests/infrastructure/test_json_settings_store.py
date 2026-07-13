from pathlib import Path

from gravitas.domain.models import PersistedSettings
from gravitas.infrastructure.settings.json_store import JsonSettingsStore


def test_roundtrip(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "settings.json")
    settings = PersistedSettings(
        addon_urls=("https://a/manifest.json", "https://b/manifest.json"),
        tmdb_key="k123",
    )
    store.save(settings)
    assert store.load() == settings


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "nope" / "settings.json")
    assert store.load() == PersistedSettings()


def test_corrupt_file_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    store = JsonSettingsStore(path)
    assert store.load() == PersistedSettings()


def test_wrong_shape_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": "not-a-list", "tmdb_key": 5}')
    store = JsonSettingsStore(path)
    assert store.load() == PersistedSettings()


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "dir" / "settings.json"
    store = JsonSettingsStore(path)
    store.save(PersistedSettings(tmdb_key="x"))
    assert store.load().tmdb_key == "x"
