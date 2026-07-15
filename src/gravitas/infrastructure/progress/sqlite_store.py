"""SQLite ProgressStore adapter (XDG data dir by default).

One row per (media_id, video_id). Writes are single-row UPSERTs into a
WITHOUT ROWID B-tree, so a save costs the same whether the table holds ten
entries or ten thousand — the whole point of not using a rewrite-the-file
format here.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from gravitas.domain.models import MediaType, PlaybackProgress

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1

_CREATE = """
CREATE TABLE IF NOT EXISTS progress (
  media_id   TEXT NOT NULL,
  video_id   TEXT NOT NULL DEFAULT '',
  type       TEXT NOT NULL,
  name       TEXT NOT NULL DEFAULT '',
  poster     TEXT,
  label      TEXT NOT NULL DEFAULT '',
  position   REAL NOT NULL,
  duration   REAL NOT NULL,
  watched    INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (media_id, video_id)
) WITHOUT ROWID
"""

_COLUMNS = "media_id, video_id, type, name, poster, label, position, duration, watched, updated_at"


def default_progress_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "gravitas" / "progress.db"


def _to_entry(row: tuple[Any, ...]) -> PlaybackProgress:
    media_type: MediaType = "series" if row[2] == "series" else "movie"
    return PlaybackProgress(
        media_id=str(row[0]),
        video_id=str(row[1]),
        type=media_type,
        name=str(row[3]),
        poster=str(row[4]) if row[4] is not None else None,
        label=str(row[5]),
        position=float(row[6]),
        duration=float(row[7]),
        watched=bool(row[8]),
        updated_at=int(row[9]),
    )


class SqliteProgressStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_progress_path()
        self._conn: sqlite3.Connection | None = None
        self._broken = False

    def _connect(self) -> sqlite3.Connection | None:
        """Open (once) and migrate. None means progress is disabled for this
        session — a bad DB must never take the app down with it."""
        if self._conn is not None:
            return self._conn
        if self._broken:
            return None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: the connection is only ever touched from
            # the GUI thread today, but the render/mpv threads share the
            # process and Qt gives no hard guarantee about which one runs a
            # queued slot.
            conn = sqlite3.connect(self._path, check_same_thread=False)
            # WAL: a writer never blocks a reader, and an unclean exit rolls
            # back instead of corrupting.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(_CREATE)
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("progress store unavailable at %s: %s", self._path, exc)
            self._broken = True
            return None
        self._conn = conn
        return conn

    def load_all(self) -> list[PlaybackProgress]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = conn.execute(f"SELECT {_COLUMNS} FROM progress").fetchall()
        except sqlite3.Error as exc:
            _log.warning("failed to read progress: %s", exc)
            return []
        return [_to_entry(row) for row in rows]

    def save(self, entry: PlaybackProgress) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute(
                f"INSERT INTO progress ({_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(media_id, video_id) DO UPDATE SET"
                " type=excluded.type, name=excluded.name, poster=excluded.poster,"
                " label=excluded.label, position=excluded.position,"
                " duration=excluded.duration, watched=excluded.watched,"
                " updated_at=excluded.updated_at",
                (
                    entry.media_id,
                    entry.video_id,
                    entry.type,
                    entry.name,
                    entry.poster,
                    entry.label,
                    entry.position,
                    entry.duration,
                    int(entry.watched),
                    entry.updated_at,
                ),
            )
            conn.commit()
        except sqlite3.Error as exc:
            _log.warning("failed to save progress for %s: %s", entry.media_id, exc)

    def delete(self, media_id: str, video_id: str | None = None) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            if video_id is None:
                conn.execute("DELETE FROM progress WHERE media_id = ?", (media_id,))
            else:
                conn.execute(
                    "DELETE FROM progress WHERE media_id = ? AND video_id = ?",
                    (media_id, video_id),
                )
            conn.commit()
        except sqlite3.Error as exc:
            _log.warning("failed to delete progress for %s: %s", media_id, exc)

    def clear(self) -> None:
        conn = self._connect()
        if conn is None:
            return
        try:
            conn.execute("DELETE FROM progress")
            conn.commit()
        except sqlite3.Error as exc:
            _log.warning("failed to clear progress: %s", exc)
