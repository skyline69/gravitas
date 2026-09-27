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


def test_pip_width_roundtrip(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "settings.json")
    store.save(PersistedSettings(pip_width=640))
    assert store.load().pip_width == 640


def test_pip_width_out_of_range_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = JsonSettingsStore(path)
    default = PersistedSettings().pip_width
    for value in (0, 239, 1281, 100000, -480):
        path.write_text(f'{{"pip_width": {value}}}')
        assert store.load().pip_width == default


def test_pip_width_bounds_are_inclusive(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = JsonSettingsStore(path)
    for value in (240, 1280):
        path.write_text(f'{{"pip_width": {value}}}')
        assert store.load().pip_width == value


def test_pip_width_corrupt_value_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = JsonSettingsStore(path)
    default = PersistedSettings().pip_width
    # A bool is an int in Python; True must not become a 1-pixel window.
    for literal in ('"480"', "true", "null", "480.5", "[480]"):
        path.write_text(f'{{"pip_width": {literal}}}')
        assert store.load().pip_width == default


def test_pip_width_missing_key_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"tmdb_key": "k"}')
    assert JsonSettingsStore(path).load().pip_width == PersistedSettings().pip_width


def test_recommendation_state_roundtrips(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "s.json")
    store.save(PersistedSettings(recommend_sources=False, decode_strain=("av1:2160", "hevc:1080")))
    loaded = store.load()
    assert loaded.recommend_sources is False
    assert loaded.decode_strain == ("av1:2160", "hevc:1080")


def test_a_settings_file_from_before_the_marks_keeps_them_on(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text('{"addon_urls": []}', encoding="utf-8")
    loaded = JsonSettingsStore(path).load()
    assert loaded.recommend_sources is True
    assert loaded.decode_strain == ()


def test_junk_decode_strain_is_dropped_rather_than_loaded(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    path.write_text('{"decode_strain": ["av1:2160", 7, null, ""]}', encoding="utf-8")
    assert JsonSettingsStore(path).load().decode_strain == ("av1:2160",)


def test_parallel_streaming_roundtrips_and_defaults_on(tmp_path: Path) -> None:
    store = JsonSettingsStore(tmp_path / "s.json")
    for choice in (True, False):
        store.save(PersistedSettings(parallel_streaming=choice))
        assert store.load().parallel_streaming is choice, "a choice made now is kept"

    path = tmp_path / "fresh.json"
    path.write_text('{"addon_urls": []}', encoding="utf-8")
    assert JsonSettingsStore(path).load().parallel_streaming is True


def test_an_old_off_is_read_as_the_old_default_not_a_choice(tmp_path: Path) -> None:
    # Every file saved while the default was off holds "parallel_streaming":
    # false whether or not anyone chose it -- settings are written whole.
    path = tmp_path / "old.json"
    path.write_text('{"parallel_streaming": false}', encoding="utf-8")
    assert JsonSettingsStore(path).load().parallel_streaming is True
    # An old "on" was always a choice.
    path.write_text('{"parallel_streaming": true}', encoding="utf-8")
    assert JsonSettingsStore(path).load().parallel_streaming is True
    # Once written again, the new key carries the choice -- including off.
    store = JsonSettingsStore(path)
    store.save(PersistedSettings(parallel_streaming=False))
    assert JsonSettingsStore(path).load().parallel_streaming is False
