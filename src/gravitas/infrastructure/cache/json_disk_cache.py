"""SQLite-backed JSON response cache (XDG cache dir by default).

The persistent layer under AddonClient's in-memory TtlCache: catalogs, meta
and manifests survive a restart, so a warm launch paints from disk instead of
refetching and reparsing the network's worth of JSON. Entries carry their
wall-clock age rather than a verdict — the caller decides what counts as
fresh (its TTLs) and whether stale is acceptable (the boot-time
stale-while-revalidate path).

Wall clock on purpose, unlike TtlCache's monotonic: ages are compared across
process lifetimes, where a monotonic reading means nothing. A clock jump can
mis-age an entry; the cost is one avoidable refetch, not corruption.

Same never-raises contract as the other sqlite stores: a broken database
disables caching for the session, it never takes the app down.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Entries untouched for this long are dropped at startup. Everything cached
# here refreshes on a much shorter cadence; this only stops the file growing
# with URLs the user never visits again.
PRUNE_AGE = 7 * 24 * 60 * 60.0

_CREATE = """
CREATE TABLE IF NOT EXISTS cache (
  url       TEXT NOT NULL PRIMARY KEY,
  payload   TEXT NOT NULL,
  stored_at REAL NOT NULL
) WITHOUT ROWID
"""


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "gravitas" / "addon-json.db"


class JsonDiskCache:
    """Callers may use this from worker threads (AddonClient reads and
    writes via asyncio.to_thread); a lock serializes connection use."""

    def __init__(
        self,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path if path is not None else default_cache_path()
        self._clock = clock
        self._conn: sqlite3.Connection | None = None
        self._broken = False
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if self._broken:
            return None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(_CREATE)
            conn.commit()
        except (sqlite3.Error, OSError) as exc:
            _log.warning("json disk cache unavailable at %s: %s", self._path, exc)
            self._broken = True
            return None
        self._conn = conn
        return conn

    def get(self, url: str) -> tuple[dict[str, Any], float] | None:
        """The cached payload and its age in seconds, or None. Freshness is
        the caller's call — it knows its TTLs and whether stale will do."""
        with self._lock:
            conn = self._connect()
            if conn is None:
                return None
            try:
                row = conn.execute(
                    "SELECT payload, stored_at FROM cache WHERE url = ?", (url,)
                ).fetchone()
            except (sqlite3.Error, OSError) as exc:
                _log.warning("json disk cache read failed for %s: %s", url, exc)
                return None
        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        return data, max(0.0, self._clock() - float(row[1]))

    def put(self, url: str, data: dict[str, Any]) -> None:
        try:
            payload = json.dumps(data, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            _log.warning("unserializable payload for %s: %s", url, exc)
            return
        with self._lock:
            conn = self._connect()
            if conn is None:
                return
            try:
                conn.execute(
                    "INSERT INTO cache (url, payload, stored_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(url) DO UPDATE SET"
                    " payload=excluded.payload, stored_at=excluded.stored_at",
                    (url, payload, self._clock()),
                )
                conn.commit()
            except (sqlite3.Error, OSError) as exc:
                _log.warning("json disk cache write failed for %s: %s", url, exc)

    def prune(self, max_age: float = PRUNE_AGE) -> int:
        """Drop entries older than `max_age` seconds; returns how many."""
        with self._lock:
            conn = self._connect()
            if conn is None:
                return 0
            try:
                cursor = conn.execute(
                    "DELETE FROM cache WHERE stored_at < ?", (self._clock() - max_age,)
                )
                conn.commit()
                return int(cursor.rowcount)
            except (sqlite3.Error, OSError) as exc:
                _log.warning("json disk cache prune failed: %s", exc)
                return 0
