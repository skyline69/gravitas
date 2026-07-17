"""SQLite WatchlistStore adapter (XDG data dir by default).

One row per media_id. Same failure policy as the progress store: a bad
database disables the watchlist for the session instead of taking the app
down, and every method degrades to a no-op / empty read.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from gravitas.domain.models import MediaType, WatchlistEntry

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_CREATE = """
CREATE TABLE IF NOT EXISTS watchlist (
  media_id TEXT NOT NULL,
  type     TEXT NOT NULL,
  name     TEXT NOT NULL DEFAULT '',
  poster   TEXT,
  year     TEXT,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (media_id)
) WITHOUT ROWID
"""

_COLUMNS = "media_id, type, name, poster, year, added_at"


def default_watchlist_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "gravitas" / "watchlist.db"


def _to_entry(row: tuple[Any, ...]) -> WatchlistEntry:
    media_type: MediaType = "series" if row[1] == "series" else "movie"
    return WatchlistEntry(
        media_id=str(row[0]),
        type=media_type,
        name=str(row[2]),
        poster=str(row[3]) if row[3] is not None else None,
        year=str(row[4]) if row[4] is not None else None,
        added_at=int(row[5]),
    )


class SqliteWatchlistStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_watchlist_path()
        self._conn: sqlite3.Connection | None = None
        self._broken = False

    def _connect(self) -> sqlite3.Connection | None:
        """Open (once) and migrate. None means the watchlist is disabled for
        this session — a bad DB must never take the app down with it."""
        if self._conn is not None:
            return self._conn
        if self._broken:
            return None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Same caveats as SqliteProgressStore: every caller is on the GUI
            # thread, and this lazy open is not thread-safe.
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(_CREATE)
            # Fine while there is one version; a real migration must read
            # user_version first and branch on it.
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("watchlist store unavailable at %s: %s", self._path, exc)
            self._broken = True
            return None
        self._conn = conn
        return conn

    def load_all(self) -> list[WatchlistEntry]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM watchlist").fetchall()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("failed to read watchlist: %s", exc)
            return []
        return [_to_entry(row) for row in rows]

    def save(self, entry: WatchlistEntry) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(
                f"INSERT INTO watchlist ({_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(media_id) DO UPDATE SET"
                " type=excluded.type, name=excluded.name, poster=excluded.poster,"
                " year=excluded.year, added_at=excluded.added_at",
                (
                    entry.media_id,
                    entry.type,
                    entry.name,
                    entry.poster,
                    entry.year,
                    entry.added_at,
                ),
            )
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("failed to save watchlist entry %s: %s", entry.media_id, exc)

    def delete(self, media_id: str) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM watchlist WHERE media_id = ?", (media_id,))
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("failed to delete watchlist entry %s: %s", media_id, exc)

    def clear(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM watchlist")
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("failed to clear watchlist: %s", exc)
