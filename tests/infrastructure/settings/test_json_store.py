from pathlib import Path

from gravitas.domain.models import PersistedSettings, TraktAuth
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


def test_trakt_session_round_trips(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    auth = TraktAuth("ACCESS", "REFRESH", 1_700_000_000, "sky")
    store.save(PersistedSettings(trakt_auth=auth))
    assert store.load().trakt_auth == auth


def test_trakt_defaults_absent(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings())
    assert store.load().trakt_auth is None


def test_malformed_trakt_block_loads_as_disconnected(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(
        '{"addon_urls": [], "trakt": {"access_token": 42, "refresh_token": "R"}}',
        encoding="utf-8",
    )
    assert JsonSettingsStore(path).load().trakt_auth is None


def test_onboarding_done_round_trips(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(onboarding_done=True))
    assert store.load().onboarding_done is True
    store.save(PersistedSettings(onboarding_done=False))
    assert store.load().onboarding_done is False


def test_no_settings_file_means_onboarding_pending(tmp_path: Path):
    # A brand-new install has no settings file: onboarding must show.
    assert JsonSettingsStore(tmp_path / "settings.json").load().onboarding_done is False


def test_existing_settings_file_without_flag_skips_onboarding(tmp_path: Path):
    # A settings file from a pre-onboarding version belongs to an established
    # user: never show them the first-run wizard on upgrade.
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": ["https://x/manifest.json"]}', encoding="utf-8")
    assert JsonSettingsStore(path).load().onboarding_done is True
