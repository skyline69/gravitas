from pathlib import Path

from gravitas.domain.models import PersistedSettings
from gravitas.infrastructure.settings.json_store import JsonSettingsStore


def test_mdblist_key_round_trips(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(addon_urls=("https://x/manifest.json",), mdblist_key="abc123"))
    loaded = store.load()
    assert loaded.mdblist_key == "abc123"
    assert loaded.addon_urls == ("https://x/manifest.json",)


def test_missing_mdblist_key_defaults_none(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(addon_urls=()))
    assert store.load().mdblist_key is None


def test_empty_string_mdblist_key_loads_as_none(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": [], "mdblist_key": ""}', encoding="utf-8")
    assert JsonSettingsStore(path).load().mdblist_key is None
