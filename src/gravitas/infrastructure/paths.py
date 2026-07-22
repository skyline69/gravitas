"""Where Gravitas keeps its files, per platform.

One module so the five stores (progress, watchlist, settings, JSON cache,
artwork cache) cannot drift apart. Unix keeps the XDG layout it has always
used -- including on macOS, where moving to ~/Library/Application Support
would orphan every existing install's database. Windows has no XDG, so the
native environment is used instead:

    config -> %APPDATA%\\Gravitas          (roaming: settings.json is small
                                            and worth following the user)
    data   -> %LOCALAPPDATA%\\Gravitas     (SQLite files: never roam these,
                                            a half-synced database is worse
                                            than a local one)
    cache  -> %LOCALAPPDATA%\\Gravitas\\Cache

Every function takes its environment and platform as arguments so the branch
that does not match the host is still testable.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

# Windows convention is a capitalised, spelled-out folder; XDG's is lowercase.
_WINDOWS_DIR = "Gravitas"
_XDG_DIR = "gravitas"

WINDOWS = "win32"


def _windows_root(environ: Mapping[str, str], variable: str, fallback: str) -> Path:
    """`%variable%`, or the standard location under the profile when unset.

    The variables are set on every supported Windows, but a service account or
    a stripped environment can lack them and the app must still start.
    """
    value = environ.get(variable, "")
    if value:
        return Path(value)
    return Path.home() / "AppData" / fallback


def _xdg_root(environ: Mapping[str, str], variable: str, *fallback: str) -> Path:
    value = environ.get(variable, "")
    if value:
        return Path(value)
    return Path.home().joinpath(*fallback)


def config_dir(environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """User configuration (settings.json)."""
    if platform == WINDOWS:
        return _windows_root(environ, "APPDATA", "Roaming") / _WINDOWS_DIR
    return _xdg_root(environ, "XDG_CONFIG_HOME", ".config") / _XDG_DIR


def data_dir(environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """Durable user data (progress.db, watchlist.db) -- losing it hurts."""
    if platform == WINDOWS:
        return _windows_root(environ, "LOCALAPPDATA", "Local") / _WINDOWS_DIR
    return _xdg_root(environ, "XDG_DATA_HOME", ".local", "share") / _XDG_DIR


def cache_dir(environ: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """Re-downloadable data (addon JSON, artwork) -- deleting it costs only
    bandwidth, which is exactly why it must not live beside the databases."""
    if platform == WINDOWS:
        return _windows_root(environ, "LOCALAPPDATA", "Local") / _WINDOWS_DIR / "Cache"
    return _xdg_root(environ, "XDG_CACHE_HOME", ".cache") / _XDG_DIR
