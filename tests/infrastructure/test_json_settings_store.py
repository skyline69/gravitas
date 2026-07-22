from pathlib import Path

from gravitas.domain.models import PersistedSettings, SubtitleStyle
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
    # onboarding_done=True: any parseable settings file predates this launch,
    # so its owner is an established user who must not see the wizard.
    assert store.load() == PersistedSettings(onboarding_done=True)


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "dir" / "settings.json"
    store = JsonSettingsStore(path)
    store.save(PersistedSettings(tmdb_key="x"))
    assert store.load().tmdb_key == "x"


def test_subtitle_style_roundtrip(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "settings.json")
    style = SubtitleStyle(font_size=70, color="#FFE400", border_size=1, back_opacity=50, bold=True)
    store.save(PersistedSettings(subtitle_style=style))
    assert store.load().subtitle_style == style


def test_subtitle_style_defaults_when_missing_or_junk(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": [], "subtitle_style": {"font_size": "big", "color": 5}}')
    assert JsonSettingsStore(path).load().subtitle_style == SubtitleStyle()


def test_trakt_sync_flags_roundtrip(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(trakt_sync_forgets=False, trakt_sync_watched=False))
    loaded = store.load()
    assert loaded.trakt_sync_forgets is False
    assert loaded.trakt_sync_watched is False


def test_trakt_sync_flags_default_on_when_missing(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"addon_urls": []}')  # pre-flag settings file
    loaded = JsonSettingsStore(path).load()
    assert loaded.trakt_sync_forgets is True
    assert loaded.trakt_sync_watched is True
