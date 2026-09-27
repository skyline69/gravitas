from pathlib import Path

from gravitas.domain.models import ConnectionSample, PersistedSettings, TrackLanguages, TraktAuth
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


def test_connection_sort_round_trips(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(sort_by_connection=True))
    assert store.load().sort_by_connection is True
    store.save(PersistedSettings(sort_by_connection=False))
    assert store.load().sort_by_connection is False


def test_connection_sort_defaults_off_for_an_existing_install(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": []}', encoding="utf-8")
    assert JsonSettingsStore(path).load().sort_by_connection is False


def test_connection_samples_round_trip(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    samples = (
        ConnectionSample(kbps=42_000, at=1_700_000_000, bucket="wifi"),
        ConnectionSample(kbps=500_000, at=1_700_000_100, bucket="ethernet"),
    )
    store.save(PersistedSettings(connection_samples=samples))
    assert store.load().connection_samples == samples


def test_broken_samples_are_skipped_not_fatal(tmp_path: Path):
    # Each entry here is wrong in a different way; the one good sample must
    # survive, and none of the others may become a plausible-looking rate.
    path = tmp_path / "settings.json"
    path.write_text(
        """{"connection_samples": [
            {"kbps": "fast", "at": 1700000000, "bucket": "wifi"},
            {"kbps": 1000, "at": 1700000000, "bucket": "carrier-pigeon"},
            {"kbps": 0, "at": 1700000000, "bucket": "wifi"},
            {"kbps": 1000, "bucket": "wifi"},
            "not a sample",
            {"kbps": 9000, "at": 1700000000, "bucket": "ethernet"}
        ]}""",
        encoding="utf-8",
    )
    loaded = JsonSettingsStore(path).load().connection_samples
    assert loaded == (ConnectionSample(kbps=9000, at=1_700_000_000, bucket="ethernet"),)


def test_samples_that_are_not_a_list_load_as_none(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"connection_samples": {"kbps": 5}}', encoding="utf-8")
    assert JsonSettingsStore(path).load().connection_samples == ()


def test_hide_incompatible_round_trips(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(hide_incompatible=True, incompatible_sources=("silo s01e01 dv",)))
    loaded = store.load()
    assert loaded.hide_incompatible is True
    assert loaded.incompatible_sources == ("silo s01e01 dv",)


def test_incompatible_sources_default_empty_for_an_existing_install(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": []}', encoding="utf-8")
    loaded = JsonSettingsStore(path).load()
    assert loaded.hide_incompatible is False
    assert loaded.incompatible_sources == ()


def test_broken_signature_entries_are_skipped(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(
        '{"incompatible_sources": ["good one", 42, "", null, {"a": 1}]}', encoding="utf-8"
    )
    assert JsonSettingsStore(path).load().incompatible_sources == ("good one",)


def test_track_languages_round_trip(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(track_languages=TrackLanguages(audio="ja", subtitle="off")))
    assert store.load().track_languages == TrackLanguages(audio="ja", subtitle="off")


def test_a_settings_file_without_track_languages_has_no_preference(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": []}', encoding="utf-8")
    assert JsonSettingsStore(path).load().track_languages == TrackLanguages()


def test_unknown_track_languages_load_as_no_preference(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text('{"track_languages": {"audio": "klingon", "subtitle": 7}}', encoding="utf-8")
    assert JsonSettingsStore(path).load().track_languages == TrackLanguages()


def test_online_segments_round_trips_and_defaults_on(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(online_segments=False))
    assert store.load().online_segments is False
    older = tmp_path / "older.json"
    older.write_text('{"addon_urls": []}', encoding="utf-8")
    assert JsonSettingsStore(older).load().online_segments is True


def test_the_video_player_round_trips_and_defaults_to_native(tmp_path: Path):
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(video_player="mpv"))
    assert store.load().video_player == "mpv"
    older = tmp_path / "older.json"
    older.write_text('{"addon_urls": []}', encoding="utf-8")
    assert JsonSettingsStore(older).load().video_player == "native"
    odd = tmp_path / "odd.json"
    odd.write_text('{"video_player": "vlc"}', encoding="utf-8")
    assert JsonSettingsStore(odd).load().video_player == "native"
