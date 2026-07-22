"""Both platform branches, on whichever host runs the suite.

The functions take environ/platform explicitly for exactly this reason: a
Windows path bug that only shows up on Windows is a bug nobody sees until a
user's settings land in C:\\Users\\x\\.local\\share.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gravitas.infrastructure.paths import cache_dir, config_dir, data_dir


def test_windows_uses_appdata_and_localappdata() -> None:
    env = {
        "APPDATA": r"C:\Users\ada\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\ada\AppData\Local",
    }
    assert config_dir(env, "win32") == Path(r"C:\Users\ada\AppData\Roaming") / "Gravitas"
    assert data_dir(env, "win32") == Path(r"C:\Users\ada\AppData\Local") / "Gravitas"
    assert cache_dir(env, "win32") == Path(r"C:\Users\ada\AppData\Local") / "Gravitas" / "Cache"


def test_windows_databases_never_land_in_the_roaming_profile() -> None:
    # A SQLite file synced between machines mid-write is a corrupted library;
    # settings.json is small and idempotent, so only it roams.
    env = {"APPDATA": r"C:\roam", "LOCALAPPDATA": r"C:\local"}
    assert not data_dir(env, "win32").is_relative_to(Path(r"C:\roam"))
    assert not cache_dir(env, "win32").is_relative_to(Path(r"C:\roam"))


def test_windows_falls_back_to_the_profile_when_the_variables_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A stripped environment (a service account, a bare `runas`) must not stop
    # the app from starting.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert config_dir({}, "win32") == tmp_path / "AppData" / "Roaming" / "Gravitas"
    assert data_dir({}, "win32") == tmp_path / "AppData" / "Local" / "Gravitas"


def test_unix_keeps_the_xdg_layout() -> None:
    env = {
        "XDG_CONFIG_HOME": "/x/config",
        "XDG_DATA_HOME": "/x/data",
        "XDG_CACHE_HOME": "/x/cache",
    }
    assert config_dir(env, "linux") == Path("/x/config/gravitas")
    assert data_dir(env, "linux") == Path("/x/data/gravitas")
    assert cache_dir(env, "linux") == Path("/x/cache/gravitas")


def test_macos_keeps_the_xdg_layout_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Deliberate: switching macOS to ~/Library/Application Support would strand
    # every existing install's databases where nothing looks for them.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert data_dir({}, "darwin") == tmp_path / ".local" / "share" / "gravitas"
    assert config_dir({}, "darwin") == tmp_path / ".config" / "gravitas"
    assert cache_dir({}, "darwin") == tmp_path / ".cache" / "gravitas"
