# Watch Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist playback position locally so movies and episodes resume where the user left off, show that progress on episode rows and poster cards, and let the user forget it per item, per title, or entirely.

**Architecture:** A SQLite table behind a `ProgressStore` port, fronted by a `WatchProgressRepository` in `application` that holds the whole table in two dicts and owns every policy rule. Reads are dict hits (they happen per grid cell); writes are single-row UPSERTs on a 5s timer during playback. Resume rides mpv's `start` option at load time, so there is no seek-after-load race.

**Tech Stack:** Python 3.14, PySide6 (Qt6/QML), stdlib `sqlite3`, pytest, uv.

## Global Constraints

- Clean Architecture dependency rule: `presentation → application → domain ← infrastructure`. **`application` must never import `infrastructure`.** `domain` imports nothing but stdlib.
- Three quality gates pass on every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- `mypy --strict` covers `src` only. `python-mpv` and `qasync` are untyped — use precise `# type: ignore[code]`, never blanket ignores.
- Tests must never require libmpv. Player logic is tested through the `FakePlayer` seam.
- Async controller slots use `@qasync.asyncSlot`, never `@Slot`. (No new async slots in this plan — every progress path is synchronous.)
- Every new QML context property must be added to `engine._gravitas_refs` in `main.py` or it is garbage-collected to null.
- Before adding a model role, confirm no QML id in a consuming delegate shares its name. `progressFraction` and `watched` were checked against every delegate and are clear.
- Store adapters never raise: a missing, corrupt, or unwritable database logs a warning and degrades to progress-disabled. This mirrors `JsonSettingsStore`.
- Policy constants, defined once in `application/watch_progress.py` and used everywhere: `MIN_POSITION = 30.0`, `WATCHED_AT = 0.9`.

## File Structure

**Create:**
- `src/gravitas/presentation/models/progress_roles.py` — the movie-vs-series progress lookup, shared by every poster model.
- `src/gravitas/infrastructure/progress/__init__.py` — package marker.
- `src/gravitas/infrastructure/progress/sqlite_store.py` — `SqliteProgressStore`, `default_progress_path`.
- `src/gravitas/application/watch_progress.py` — `WatchProgressRepository`, `MIN_POSITION`, `WATCHED_AT`.
- `src/gravitas/presentation/controllers/progress_controller.py` — `ProgressController`.
- `src/gravitas/presentation/models/watched_list_model.py` — `WatchedListModel`.
- `src/gravitas/presentation/qml/components/ContextMenu.qml` — right-click menu primitive.
- `src/gravitas/presentation/qml/components/ConfirmDialog.qml` — modal confirm primitive.
- `tests/infrastructure/test_sqlite_progress_store.py`
- `tests/application/test_watch_progress.py`
- `tests/presentation/test_progress_controller.py`
- `tests/presentation/test_watched_list_model.py`

**Modify:**
- `src/gravitas/domain/models.py` — add `PlaybackProgress`.
- `src/gravitas/domain/ports.py` — add `ProgressStore`; add `start` keyword to `MediaPlayer.play`.
- `src/gravitas/infrastructure/player/mpv_player.py:224-229` — `play(url, *, start)`.
- `src/gravitas/presentation/controllers/player_controller.py` — media context, resume, autosave timer.
- `src/gravitas/presentation/controllers/detail_controller.py` — `mediaId`/`mediaType`/`mediaContext()`/episode label.
- `src/gravitas/presentation/models/episode_list_model.py` — two progress roles.
- `src/gravitas/presentation/models/poster_grid_model.py` — two progress roles.
- `src/gravitas/presentation/models/catalog_rows_model.py` — pass the repo to nested poster models.
- `src/gravitas/presentation/models/search_results_model.py` — two progress roles.
- `src/gravitas/presentation/qml/components/Icons.qml` — `check` glyph.
- `src/gravitas/presentation/qml/components/EpisodeRow.qml` — bar, checkmark, context menu.
- `src/gravitas/presentation/qml/components/PosterCard.qml` — bar, badge, context menu.
- `src/gravitas/presentation/qml/Detail.qml` — set media context before play; Forget button.
- `src/gravitas/presentation/qml/Sources.qml` — set media context before play.
- `src/gravitas/presentation/qml/Player.qml` — resume toast.
- `src/gravitas/presentation/qml/Settings.qml` — Watch progress card.
- `src/gravitas/main.py` — wiring.
- `tests/presentation/test_player_controller.py` — `FakePlayer.play` signature.

Note on QML registration: `components/qmldir` lists only some components; `EpisodeRow` and `SettingsCard` resolve through the directory import without being listed. The two new components follow that precedent — **do not** edit `qmldir`.

---

### Task 1: Domain model and port

**Files:**
- Modify: `src/gravitas/domain/models.py`
- Modify: `src/gravitas/domain/ports.py`
- Test: `tests/domain/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `PlaybackProgress` (frozen dataclass, fields `media_id: str`, `video_id: str`, `type: MediaType`, `name: str`, `poster: str | None`, `label: str`, `position: float`, `duration: float`, `watched: bool`, `updated_at: int`; property `fraction: float`). `ProgressStore` Protocol with `load_all() -> list[PlaybackProgress]`, `save(entry: PlaybackProgress) -> None`, `delete(media_id: str, video_id: str | None = None) -> None`, `clear() -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/domain/test_models.py`:

```python
def test_playback_progress_fraction() -> None:
    from gravitas.domain.models import PlaybackProgress

    def entry(**kw: object) -> PlaybackProgress:
        base: dict[str, object] = {
            "media_id": "tt1",
            "video_id": "",
            "type": "movie",
            "name": "Movie",
            "poster": None,
            "label": "",
            "position": 0.0,
            "duration": 0.0,
            "watched": False,
            "updated_at": 0,
        }
        base.update(kw)
        return PlaybackProgress(**base)  # type: ignore[arg-type]

    assert entry(position=50.0, duration=200.0).fraction == 0.25
    # Watched entries drop their position; the bar must still read full.
    assert entry(position=0.0, duration=200.0, watched=True).fraction == 1.0
    # Duration is unknown until mpv parses the file.
    assert entry(position=50.0, duration=0.0).fraction == 0.0
    # A position past a stale duration must not overflow the bar.
    assert entry(position=300.0, duration=200.0).fraction == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py::test_playback_progress_fraction -v`
Expected: FAIL with `ImportError: cannot import name 'PlaybackProgress'`

- [ ] **Step 3: Add the model**

Append to `src/gravitas/domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class PlaybackProgress:
    """One resumable position. `video_id` is "" for movies.

    `name`, `poster` and `label` are denormalized onto the entry so the
    Settings list can render an item without refetching its meta.
    """

    media_id: str
    video_id: str
    type: MediaType
    name: str
    poster: str | None
    label: str
    position: float
    duration: float
    watched: bool
    updated_at: int

    @property
    def fraction(self) -> float:
        """0.0–1.0, for a progress bar."""
        if self.watched:
            # A watched entry has had its position zeroed, but reads as done.
            return 1.0
        if self.duration <= 0:
            return 0.0
        return min(1.0, self.position / self.duration)
```

- [ ] **Step 4: Add the port**

In `src/gravitas/domain/ports.py`, add `PlaybackProgress` to the existing `from gravitas.domain.models import (...)` block (keep the list alphabetical: it goes after `MetaDetail`), then append at the end of the file:

```python
@runtime_checkable
class ProgressStore(Protocol):
    """Durable store for playback progress. load_all() must never raise on
    missing or corrupt data — it returns an empty list instead."""

    def load_all(self) -> list[PlaybackProgress]: ...
    def save(self, entry: PlaybackProgress) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None:
        """video_id None removes every entry for the media (a whole series)."""
        ...

    def clear(self) -> None: ...
```

- [ ] **Step 5: Run tests and gates**

Run: `uv run pytest tests/domain -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/domain/models.py src/gravitas/domain/ports.py tests/domain/test_models.py
git commit -m "feat(domain): PlaybackProgress model and ProgressStore port"
```

---

### Task 2: SQLite progress store

**Files:**
- Create: `src/gravitas/infrastructure/progress/__init__.py`
- Create: `src/gravitas/infrastructure/progress/sqlite_store.py`
- Test: `tests/infrastructure/test_sqlite_progress_store.py`

**Interfaces:**
- Consumes: `PlaybackProgress`, `ProgressStore` (Task 1).
- Produces: `SqliteProgressStore(path: Path | None = None)` implementing `ProgressStore`; `default_progress_path() -> Path`.

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/test_sqlite_progress_store.py`:

```python
from pathlib import Path

from gravitas.domain.models import PlaybackProgress
from gravitas.infrastructure.progress.sqlite_store import (
    SqliteProgressStore,
    default_progress_path,
)


def entry(media_id: str = "tt1", video_id: str = "", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "The Movie",
        "poster": "http://p/1.jpg",
        "label": "",
        "position": 120.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def test_round_trip(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry())
    assert SqliteProgressStore(tmp_path / "progress.db").load_all() == [entry()]


def test_save_upserts_rather_than_duplicates(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry(position=120.0))
    store.save(entry(position=300.0, updated_at=200))
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].position == 300.0


def test_poster_none_survives(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry(poster=None))
    assert store.load_all()[0].poster is None


def test_watched_flag_survives(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry(watched=True, position=0.0))
    assert store.load_all()[0].watched is True


def test_delete_one_video_leaves_siblings(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry("tt9", "tt9:1:1", type="series"))
    store.save(entry("tt9", "tt9:1:2", type="series"))
    store.delete("tt9", "tt9:1:1")
    assert [e.video_id for e in store.load_all()] == ["tt9:1:2"]


def test_delete_whole_media(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry("tt9", "tt9:1:1", type="series"))
    store.save(entry("tt9", "tt9:1:2", type="series"))
    store.save(entry("tt1", ""))
    store.delete("tt9")
    assert [e.media_id for e in store.load_all()] == ["tt1"]


def test_clear(tmp_path: Path) -> None:
    store = SqliteProgressStore(tmp_path / "progress.db")
    store.save(entry())
    store.clear()
    assert store.load_all() == []


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    assert SqliteProgressStore(tmp_path / "nope" / "progress.db").load_all() == []


def test_corrupt_file_loads_empty_and_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "progress.db"
    path.write_text("this is not a database", encoding="utf-8")
    store = SqliteProgressStore(path)
    assert store.load_all() == []
    # Every mutation must degrade quietly too.
    store.save(entry())
    store.delete("tt1")
    store.clear()


def test_schema_version_recorded(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "progress.db"
    SqliteProgressStore(path).save(entry())
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()


def test_default_path_follows_xdg_data_home(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_progress_path() == tmp_path / "gravitas" / "progress.db"
```

The `MonkeyPatch` import goes at the top of the file: `from pytest import MonkeyPatch`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_sqlite_progress_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gravitas.infrastructure.progress'`

- [ ] **Step 3: Create the package marker**

Create `src/gravitas/infrastructure/progress/__init__.py` as an empty file.

- [ ] **Step 4: Write the store**

Create `src/gravitas/infrastructure/progress/sqlite_store.py`:

```python
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

_COLUMNS = (
    "media_id, video_id, type, name, poster, label, position, duration, watched, updated_at"
)


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
```

- [ ] **Step 5: Run tests and gates**

Run: `uv run pytest tests/infrastructure/test_sqlite_progress_store.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass. If `test_corrupt_file_loads_empty_and_never_raises` fails, `_connect` is letting `sqlite3.DatabaseError` escape — the `CREATE TABLE` on a garbage file is what raises, and it must be inside the try.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/infrastructure/progress tests/infrastructure/test_sqlite_progress_store.py
git commit -m "feat(infrastructure): SQLite progress store"
```

---

### Task 3: WatchProgressRepository

**Files:**
- Create: `src/gravitas/application/watch_progress.py`
- Test: `tests/application/test_watch_progress.py`

**Interfaces:**
- Consumes: `PlaybackProgress`, `ProgressStore` (Task 1).
- Produces: `MIN_POSITION = 30.0`, `WATCHED_AT = 0.9`, and `WatchProgressRepository(store: ProgressStore, clock: Callable[[], int] = ...)` with:
  - `get(media_id: str, video_id: str = "") -> PlaybackProgress | None`
  - `latest_for(media_id: str) -> PlaybackProgress | None`
  - `fraction_for(media_id: str, video_id: str = "") -> float`
  - `is_watched(media_id: str, video_id: str = "") -> bool`
  - `in_progress() -> list[PlaybackProgress]`
  - `resume_position(media_id: str, video_id: str = "") -> float`
  - `record(*, media_id: str, video_id: str, type: MediaType, name: str, poster: str | None, label: str, position: float, duration: float) -> None`
  - `mark_watched(*, media_id: str, video_id: str, type: MediaType, name: str, poster: str | None, label: str) -> None`
  - `forget(media_id: str, video_id: str | None = None) -> None`
  - `reset_all() -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/application/test_watch_progress.py`:

```python
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import PlaybackProgress


class FakeStore:
    def __init__(self, entries: list[PlaybackProgress] | None = None) -> None:
        self.entries = list(entries or [])
        self.saved: list[PlaybackProgress] = []
        self.deleted: list[tuple[str, str | None]] = []
        self.cleared = False

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None:
        self.saved.append(entry)

    def delete(self, media_id: str, video_id: str | None = None) -> None:
        self.deleted.append((media_id, video_id))

    def clear(self) -> None:
        self.cleared = True


def entry(
    media_id: str = "tt1", video_id: str = "", **kw: object
) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "The Movie",
        "poster": None,
        "label": "",
        "position": 120.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def repo(store: FakeStore, now: int = 500) -> WatchProgressRepository:
    return WatchProgressRepository(store, clock=lambda: now)


def test_loads_index_from_store_at_construction() -> None:
    r = repo(FakeStore([entry()]))
    assert r.get("tt1") == entry()
    assert r.fraction_for("tt1") == 0.2


def test_fraction_for_unknown_is_zero() -> None:
    assert repo(FakeStore()).fraction_for("nope") == 0.0


def test_record_below_floor_is_ignored() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1", video_id="", type="movie", name="M", poster=None,
        label="", position=29.9, duration=600.0,
    )
    assert store.saved == []
    assert r.get("tt1") is None


def test_record_at_floor_is_kept() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1", video_id="", type="movie", name="M", poster=None,
        label="", position=30.0, duration=600.0,
    )
    assert len(store.saved) == 1
    assert r.get("tt1") is not None


def test_record_past_ninety_percent_marks_watched_and_zeroes_position() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1", video_id="", type="movie", name="M", poster=None,
        label="", position=540.0, duration=600.0,
    )
    saved = store.saved[0]
    assert saved.watched is True
    assert saved.position == 0.0
    assert r.resume_position("tt1") == 0.0  # a replay starts clean
    assert r.fraction_for("tt1") == 1.0


def test_record_with_unknown_duration_never_marks_watched() -> None:
    store = FakeStore()
    r = repo(store)
    r.record(
        media_id="tt1", video_id="", type="movie", name="M", poster=None,
        label="", position=100.0, duration=0.0,
    )
    assert store.saved[0].watched is False


def test_record_stamps_the_clock() -> None:
    store = FakeStore()
    repo(store, now=777).record(
        media_id="tt1", video_id="", type="movie", name="M", poster=None,
        label="", position=100.0, duration=600.0,
    )
    assert store.saved[0].updated_at == 777


def test_resume_position_returns_saved_position() -> None:
    assert repo(FakeStore([entry(position=300.0)])).resume_position("tt1") == 300.0


def test_latest_for_picks_highest_updated_at() -> None:
    store = FakeStore([
        entry("tt9", "tt9:1:1", type="series", updated_at=100, label="S1E1"),
        entry("tt9", "tt9:1:3", type="series", updated_at=300, label="S1E3"),
        entry("tt9", "tt9:1:2", type="series", updated_at=200, label="S1E2"),
    ])
    latest = repo(store).latest_for("tt9")
    assert latest is not None
    assert latest.label == "S1E3"


def test_forget_one_episode_rebuilds_latest() -> None:
    store = FakeStore([
        entry("tt9", "tt9:1:1", type="series", updated_at=100, label="S1E1"),
        entry("tt9", "tt9:1:3", type="series", updated_at=300, label="S1E3"),
    ])
    r = repo(store)
    r.forget("tt9", "tt9:1:3")
    latest = r.latest_for("tt9")
    assert latest is not None
    assert latest.label == "S1E1"  # not a stale pointer at the deleted row
    assert store.deleted == [("tt9", "tt9:1:3")]


def test_forget_last_episode_drops_latest_entirely() -> None:
    store = FakeStore([entry("tt9", "tt9:1:1", type="series")])
    r = repo(store)
    r.forget("tt9", "tt9:1:1")
    assert r.latest_for("tt9") is None


def test_forget_whole_media_drops_every_episode() -> None:
    store = FakeStore([
        entry("tt9", "tt9:1:1", type="series"),
        entry("tt9", "tt9:1:2", type="series"),
    ])
    r = repo(store)
    r.forget("tt9")
    assert r.latest_for("tt9") is None
    assert r.get("tt9", "tt9:1:1") is None
    assert store.deleted == [("tt9", None)]


def test_in_progress_is_latest_per_media_newest_first_unwatched_only() -> None:
    store = FakeStore([
        entry("tt1", "", updated_at=100),
        entry("tt9", "tt9:1:1", type="series", updated_at=400),
        entry("tt9", "tt9:1:2", type="series", updated_at=500),
        entry("tt5", "", updated_at=900, watched=True),
    ])
    rows = repo(store).in_progress()
    assert [(e.media_id, e.video_id) for e in rows] == [("tt9", "tt9:1:2"), ("tt1", "")]


def test_mark_watched_without_prior_entry() -> None:
    store = FakeStore()
    r = repo(store)
    r.mark_watched(
        media_id="tt9", video_id="tt9:1:1", type="series", name="Show",
        poster=None, label="S1E1",
    )
    assert r.is_watched("tt9", "tt9:1:1") is True
    assert store.saved[0].position == 0.0


def test_reset_all_empties_index_and_store() -> None:
    store = FakeStore([entry(), entry("tt2")])
    r = repo(store)
    r.reset_all()
    assert r.in_progress() == []
    assert r.get("tt1") is None
    assert store.cleared is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_watch_progress.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gravitas.application.watch_progress'`

- [ ] **Step 3: Write the repository**

Create `src/gravitas/application/watch_progress.py`:

```python
"""In-memory index over a ProgressStore, and every watch-progress policy rule.

Lives in `application` (not `infrastructure`) for the same reason
AddonRepository does: `application` must not import `infrastructure`, so the
store arrives through the ProgressStore port.

The whole table is held in two dicts. Progress is read per grid cell on every
flick, so a read must never touch disk; the table is small enough (one row per
started title) that holding all of it is cheaper than any cache policy.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from gravitas.domain.models import MediaType, PlaybackProgress
from gravitas.domain.ports import ProgressStore

# Under this many seconds a play is a mis-click, not a watch worth remembering.
MIN_POSITION = 30.0
# At or past this fraction of the runtime, the title counts as finished.
WATCHED_AT = 0.9


class WatchProgressRepository:
    def __init__(
        self,
        store: ProgressStore,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._store = store
        self._clock = clock
        self._by_key: dict[tuple[str, str], PlaybackProgress] = {}
        # media_id -> most recently touched entry. What a series poster's bar
        # reads, so it must stay O(1).
        self._latest: dict[str, PlaybackProgress] = {}
        for entry in store.load_all():
            self._index(entry)

    def _index(self, entry: PlaybackProgress) -> None:
        self._by_key[(entry.media_id, entry.video_id)] = entry
        current = self._latest.get(entry.media_id)
        if current is None or entry.updated_at >= current.updated_at:
            self._latest[entry.media_id] = entry

    def _rebuild_latest(self, media_id: str) -> None:
        remaining = [e for key, e in self._by_key.items() if key[0] == media_id]
        if remaining:
            self._latest[media_id] = max(remaining, key=lambda e: e.updated_at)
        else:
            self._latest.pop(media_id, None)

    # --- reads (dict hits; no I/O) ---

    def get(self, media_id: str, video_id: str = "") -> PlaybackProgress | None:
        return self._by_key.get((media_id, video_id))

    def latest_for(self, media_id: str) -> PlaybackProgress | None:
        return self._latest.get(media_id)

    def fraction_for(self, media_id: str, video_id: str = "") -> float:
        entry = self._by_key.get((media_id, video_id))
        return entry.fraction if entry is not None else 0.0

    def is_watched(self, media_id: str, video_id: str = "") -> bool:
        entry = self._by_key.get((media_id, video_id))
        return entry is not None and entry.watched

    def in_progress(self) -> list[PlaybackProgress]:
        """One row per media (the latest touched), unwatched, newest first."""
        rows = [e for e in self._latest.values() if not e.watched]
        rows.sort(key=lambda e: e.updated_at, reverse=True)
        return rows

    def resume_position(self, media_id: str, video_id: str = "") -> float:
        entry = self._by_key.get((media_id, video_id))
        if entry is None or entry.watched:
            return 0.0
        return entry.position

    # --- writes ---

    def record(
        self,
        *,
        media_id: str,
        video_id: str,
        type: MediaType,
        name: str,
        poster: str | None,
        label: str,
        position: float,
        duration: float,
    ) -> None:
        if not media_id or position < MIN_POSITION:
            return
        finished = duration > 0 and position / duration >= WATCHED_AT
        self._put(
            PlaybackProgress(
                media_id=media_id,
                video_id=video_id,
                type=type,
                name=name,
                poster=poster,
                label=label,
                # A finished title drops its position so a replay starts clean.
                position=0.0 if finished else position,
                duration=duration,
                watched=finished,
                updated_at=self._clock(),
            )
        )

    def mark_watched(
        self,
        *,
        media_id: str,
        video_id: str,
        type: MediaType,
        name: str,
        poster: str | None,
        label: str,
    ) -> None:
        if not media_id:
            return
        existing = self._by_key.get((media_id, video_id))
        self._put(
            PlaybackProgress(
                media_id=media_id,
                video_id=video_id,
                type=type,
                name=name,
                poster=poster,
                label=label,
                position=0.0,
                duration=existing.duration if existing is not None else 0.0,
                watched=True,
                updated_at=self._clock(),
            )
        )

    def _put(self, entry: PlaybackProgress) -> None:
        self._index(entry)
        self._store.save(entry)

    def forget(self, media_id: str, video_id: str | None = None) -> None:
        if video_id is None:
            for key in [k for k in self._by_key if k[0] == media_id]:
                del self._by_key[key]
            self._latest.pop(media_id, None)
        else:
            self._by_key.pop((media_id, video_id), None)
            # _latest may have pointed at the row just removed.
            self._rebuild_latest(media_id)
        self._store.delete(media_id, video_id)

    def reset_all(self) -> None:
        self._by_key.clear()
        self._latest.clear()
        self._store.clear()
```

- [ ] **Step 4: Run tests and gates**

Run: `uv run pytest tests/application/test_watch_progress.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/application/watch_progress.py tests/application/test_watch_progress.py
git commit -m "feat(application): watch progress repository with in-memory index"
```

---

### Task 4: Resume support in the player port

**Files:**
- Modify: `src/gravitas/domain/ports.py`
- Modify: `src/gravitas/infrastructure/player/mpv_player.py:224-229`
- Test: `tests/infrastructure/player/test_mpv_player.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `MediaPlayer.play(url: str, *, start: float = 0.0) -> None` — the port signature every implementation and fake must now match.

- [ ] **Step 1: Write the failing test**

In `tests/infrastructure/player/test_mpv_player.py`, add `self.start = 0.0` to `FakeMpv.__init__` (next to `self.played`), then append — the file's `_player()` helper already returns `(MpvPlayer, FakeMpv)`:

```python
def test_play_sets_start_option_for_resume() -> None:
    player, fake = _player()
    player.play("http://s/v.mkv", start=1820.5)
    # mpv applies `start` at load time, so resume needs no seek-after-load.
    assert fake.start == 1820.5
    assert fake.played == ["http://s/v.mkv"]


def test_play_from_zero_clears_start_option() -> None:
    player, fake = _player()
    player.play("http://s/v.mkv", start=900.0)
    player.play("http://s/other.mkv")
    # A stale `start` would silently seek the NEXT file to the old position.
    assert fake.start == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/player/test_mpv_player.py -v -k start`
Expected: FAIL with `TypeError: play() got an unexpected keyword argument 'start'`

- [ ] **Step 3: Update the port**

In `src/gravitas/domain/ports.py`, in the `MediaPlayer` Protocol, replace:

```python
    def play(self, url: str) -> None: ...
```

with:

```python
    def play(self, url: str, *, start: float = 0.0) -> None:
        """Begin playback, seeking to `start` seconds at load time."""
        ...
```

- [ ] **Step 4: Update MpvPlayer**

In `src/gravitas/infrastructure/player/mpv_player.py`, replace the `play` method:

```python
    def play(self, url: str, *, start: float = 0.0) -> None:
        try:
            # `start` is applied by mpv when the file loads, so resuming needs
            # no seek-after-file-loaded race. Always assign it: mpv keeps the
            # option across loads, and a stale value would seek the next file.
            self._mpv.start = start if start > 0 else 0
            self._mpv.play(url)
            self._mpv.pause = False
        except Exception as exc:
            raise PlaybackFailed(f"failed to play {url}: {exc}") from exc
```

- [ ] **Step 5: Run tests and gates**

Run: `uv run pytest tests/infrastructure -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

The suite stays green: `start` has a default, so `PlayerController`'s existing `player.play(url)` call and the test `FakePlayer` are both still valid. Task 5 updates them.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/domain/ports.py src/gravitas/infrastructure/player/mpv_player.py tests/infrastructure/player/test_mpv_player.py
git commit -m "feat(player): start-at-position support on the MediaPlayer port"
```

---

### Task 5: PlayerController media context, resume, and autosave

**Files:**
- Modify: `src/gravitas/presentation/controllers/player_controller.py`
- Test: `tests/presentation/test_player_controller.py`

**Interfaces:**
- Consumes: `WatchProgressRepository` (Task 3), `MediaPlayer.play(url, *, start)` (Task 4).
- Produces: `PlayerController(player_factory, style_provider=None, progress: WatchProgressRepository | None = None)`; slots `setMediaContext(context: dict)`, `flushProgress()`; signals `resumed(float)`, `progressRecorded()`. The context dict keys are exactly `mediaId`, `videoId`, `type`, `name`, `poster`, `label`.

- [ ] **Step 1: Update FakePlayer for the new port signature**

In `tests/presentation/test_player_controller.py`, replace `FakePlayer.play` and add a `start` field to its `__init__` (add `self.start = 0.0` next to `self._position = 0.0`):

```python
    def play(self, url: str, *, start: float = 0.0) -> None:
        if self.fail:
            from gravitas.domain.errors import PlaybackFailed

            raise PlaybackFailed("boom")
        self.start = start
        self._position = start
        # Recorded without `start` so the existing call-order assertions hold.
        self.calls.append(f"play:{url}")
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/presentation/test_player_controller.py`:

```python
CONTEXT = {
    "mediaId": "tt9",
    "videoId": "tt9:1:1",
    "type": "series",
    "name": "The Show",
    "poster": "http://p/9.jpg",
    "label": "S1E1 · Pilot",
}


class FakeProgress:
    def __init__(self, resume: float = 0.0) -> None:
        self._resume = resume
        self.records: list[dict[str, object]] = []

    def resume_position(self, media_id: str, video_id: str = "") -> float:
        return self._resume

    def record(self, **kwargs: object) -> None:
        self.records.append(kwargs)


def test_play_resumes_from_saved_position(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress(resume=1820.5)
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert player.start == 1820.5


def test_play_emits_resumed_only_when_resuming(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress(resume=90.0))
    seen: list[float] = []
    controller.resumed.connect(seen.append)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert seen == [90.0]


def test_play_from_scratch_does_not_emit_resumed(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player, None, FakeProgress(resume=0.0))
    seen: list[float] = []
    controller.resumed.connect(seen.append)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert seen == []
    assert player.start == 0.0


def test_records_context_on_flush(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    controller.flushProgress()
    assert progress.records == [{
        "media_id": "tt9",
        "video_id": "tt9:1:1",
        "type": "series",
        "name": "The Show",
        "poster": "http://p/9.jpg",
        "label": "S1E1 · Pilot",
        "position": 300.0,
        "duration": 100.0,
    }]


def test_no_context_means_no_record(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.play("http://s/v.mkv")  # played from an unknown surface
    player.seek(300.0)
    controller.flushProgress()
    assert progress.records == []  # a nameless row helps nobody


def test_unknown_duration_means_no_record(qapp: object) -> None:
    player = FakePlayer()
    player._duration = 0.0  # mpv has not parsed the file yet
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.flushProgress()
    assert progress.records == []


def test_pause_records(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    controller.pause()
    assert len(progress.records) == 1


def test_stop_records_and_halts_the_timer(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    assert controller.is_recording() is True
    player.seek(300.0)
    controller.stop()
    assert len(progress.records) == 1
    # A timer left running would keep writing after playback ended.
    assert controller.is_recording() is False


def test_empty_poster_recorded_as_none(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext({**CONTEXT, "poster": ""})
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    controller.flushProgress()
    assert progress.records[0]["poster"] is None


def test_timer_tick_skips_while_paused(qapp: object) -> None:
    player = FakePlayer()
    progress = FakeProgress()
    controller = PlayerController(lambda: player, None, progress)
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    player.seek(300.0)
    player._paused = True
    controller._on_tick()
    assert progress.records == []


def test_works_without_a_progress_repo(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(lambda: player)  # progress disabled
    controller.setMediaContext(CONTEXT)
    controller.play("http://s/v.mkv")
    controller.flushProgress()
    assert player.start == 0.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/presentation/test_player_controller.py -v -k "resume or record or context or tick or flush"`
Expected: FAIL with `AttributeError: 'PlayerController' object has no attribute 'setMediaContext'`

- [ ] **Step 4: Implement**

In `src/gravitas/presentation/controllers/player_controller.py`, update the imports:

```python
from collections.abc import Callable

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.models import MediaType, SubtitleStyle
from gravitas.domain.ports import MediaPlayer
```

Add the two signals next to the existing ones:

```python
    resumed = Signal(float)
    progressRecorded = Signal()
```

Replace `__init__`:

```python
    # Frequent enough that a hard kill costs seconds, not minutes; rare enough
    # that a two-hour film writes ~1400 rows' worth of UPSERTs, not 7 million.
    SAVE_INTERVAL_MS = 5000

    def __init__(
        self,
        player_factory: Callable[[], MediaPlayer],
        style_provider: Callable[[], SubtitleStyle] | None = None,
        progress: WatchProgressRepository | None = None,
    ) -> None:
        super().__init__()
        self._factory = player_factory
        self._style_provider = style_provider
        self._progress = progress
        self._player: MediaPlayer | None = None
        self._context: dict[str, str] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(PlayerController.SAVE_INTERVAL_MS)
        self._save_timer.timeout.connect(self._on_tick)
```

Replace `play`, `stop` and `pause`:

```python
    @Slot(str)
    def play(self, url: str) -> None:
        player = self._ensure()
        if player is None:
            return
        start = self._resume_position()
        try:
            player.play(url, start=start)
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))
            return
        if start > 0:
            self.resumed.emit(start)
        self._save_timer.start()
        self.stateChanged.emit()

    @Slot()
    def stop(self) -> None:
        if self._player is not None:
            self._record()
            self._save_timer.stop()
            self._player.stop()

    @Slot()
    def pause(self) -> None:
        if self._player is not None:
            self._player.pause()
            self._record()
```

Add the progress block after the `# --- controls ---` methods:

```python
    # --- watch progress ---

    @Slot("QVariantMap")
    def setMediaContext(self, context: dict[str, object]) -> None:
        """Identify what is about to play. QML calls this immediately before
        play(url); without it nothing is recorded and nothing resumes.

        Keys: mediaId, videoId, type, name, poster, label.
        """
        self._context = {
            str(key): "" if value is None else str(value)
            for key, value in context.items()
        }

    @Slot()
    def flushProgress(self) -> None:
        """Record now. Wired to aboutToQuit so the last seconds survive."""
        self._record()

    def is_recording(self) -> bool:
        """True while the autosave timer is live. Not a Slot — QML has no use
        for it; it exists so a test can assert the timer's lifecycle without
        waiting out a real interval."""
        return self._save_timer.isActive()

    def _on_tick(self) -> None:
        if self._player is not None and not self._player.is_paused():
            self._record()

    def _resume_position(self) -> float:
        media_id = self._context.get("mediaId", "")
        if self._progress is None or not media_id:
            return 0.0
        return self._progress.resume_position(media_id, self._context.get("videoId", ""))

    def _record(self) -> None:
        media_id = self._context.get("mediaId", "")
        if self._progress is None or self._player is None or not media_id:
            return
        duration = self._player.duration()
        if duration <= 0:
            # mpv has not parsed the file yet; a fraction against 0 is noise.
            return
        media_type: MediaType = "series" if self._context.get("type") == "series" else "movie"
        self._progress.record(
            media_id=media_id,
            video_id=self._context.get("videoId", ""),
            type=media_type,
            name=self._context.get("name", ""),
            poster=self._context.get("poster") or None,
            label=self._context.get("label", ""),
            position=self._player.position(),
            duration=duration,
        )
        self.progressRecorded.emit()
```

- [ ] **Step 5: Run tests and gates**

Run: `uv run pytest tests/presentation/test_player_controller.py -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass, including the pre-existing tests (they construct `PlayerController` with one or two args, which still works).

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/presentation/controllers/player_controller.py tests/presentation/test_player_controller.py
git commit -m "feat(player): record playback position and auto-resume"
```

---

### Task 6: DetailController media context

**Files:**
- Modify: `src/gravitas/presentation/controllers/detail_controller.py`
- Test: `tests/presentation/test_detail_controller.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `DetailController.mediaId` / `.mediaType` Properties (notify `metaChanged`); `@Slot(result="QVariantMap") mediaContext()` returning `{mediaId, videoId, type, name, poster, label}` for the current selection — the movie when no episode is selected, otherwise the selected episode. Feeds `PlayerController.setMediaContext` (Task 5).

- [ ] **Step 1: Write the failing test**

Append to `tests/presentation/test_detail_controller.py`. The file's tests are `async def` and use the existing `FakeGetDetail` (a movie named `Film`, poster `"p"`), `FakeResolve`, and the `_series_ctl()` helper (a series named `Show`, poster `None`, episodes `tt1:1:1` / `tt1:1:2` / `tt1:2:1` / `tt1:0:1`):

```python
async def test_media_context_for_a_movie(qapp: object) -> None:
    ctl = DetailController(FakeGetDetail(), FakeResolve(), StreamListModel())  # type: ignore[arg-type]
    await ctl.load("movie", "tt2")
    assert ctl.mediaContext() == {
        "mediaId": "tt2",
        "videoId": "",
        "type": "movie",
        "name": "Film",
        "poster": "p",
        "label": "",
    }


async def test_media_context_for_a_selected_episode(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "Two")
    assert ctl.mediaContext() == {
        "mediaId": "tt1",
        "videoId": "tt1:1:2",
        "type": "series",
        "name": "Show",
        "poster": "",
        "label": "S1E2 · Two",
    }


async def test_episode_label_without_a_title(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "")
    assert ctl.mediaContext()["label"] == "S1E2"


async def test_load_resets_the_context(qapp: object) -> None:
    ctl, _stream_model, _episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    await ctl.selectEpisode("tt1:1:2", 1, 2, "Two")
    await ctl.load("series", "tt1")
    # A stale episode id would attribute the next play to the wrong episode.
    assert ctl.mediaContext()["videoId"] == ""
    assert ctl.mediaContext()["label"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/presentation/test_detail_controller.py -v -k "context or label"`
Expected: FAIL with `AttributeError: 'DetailController' object has no attribute 'mediaContext'`

- [ ] **Step 3: Implement**

In `DetailController.__init__`, add next to `self._selected_episode = ""`:

```python
        self._media_id = ""
        self._media_type: MediaType = "movie"
        self._episode_label = ""
```

Add these Properties next to the other `metaChanged` ones:

```python
    @Property(str, notify=metaChanged)
    def mediaId(self) -> str:
        return self._media_id

    @Property(str, notify=metaChanged)
    def mediaType(self) -> str:
        return self._media_type
```

Add this slot after `selectedEpisodeId`:

```python
    @Slot(result="QVariantMap")
    def mediaContext(self) -> dict[str, str]:
        """What is currently selected for playback — the movie, or the chosen
        episode. Handed to PlayerController.setMediaContext() before play()."""
        return {
            "mediaId": self._media_id,
            "videoId": self._selected_episode,
            "type": self._media_type,
            "name": self.title,
            "poster": self.poster,
            "label": self._episode_label,
        }
```

In `selectEpisode`, after `self._selected_episode = video_id`, add:

```python
        self._episode_label = f"S{season}E{episode} · {title}" if title else f"S{season}E{episode}"
```

In `load`, alongside the other reset lines (`self._selected_episode = ""`), add:

```python
        self._media_id = item_id
        self._media_type = media_type
        self._episode_label = ""
```

- [ ] **Step 4: Run tests and gates**

Run: `uv run pytest tests/presentation -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/controllers/detail_controller.py tests/presentation/test_detail_controller.py
git commit -m "feat(detail): expose the selected media context for playback"
```

---

### Task 7: Progress roles on the list models

**Files:**
- Create: `src/gravitas/presentation/models/progress_roles.py`
- Modify: `src/gravitas/presentation/models/episode_list_model.py`
- Modify: `src/gravitas/presentation/models/poster_grid_model.py`
- Modify: `src/gravitas/presentation/models/catalog_rows_model.py`
- Modify: `src/gravitas/presentation/models/search_results_model.py`
- Test: `tests/presentation/test_models.py`

**Interfaces:**
- Consumes: `WatchProgressRepository` (Task 3), `MediaItem`.
- Produces: `progress_roles.fraction_for(progress, item) -> float` and `progress_roles.is_watched(progress, item) -> bool`, both taking `WatchProgressRepository | None` and a `MediaItem`. On all four models, an optional first constructor arg `progress: WatchProgressRepository | None = None`, a `refresh_progress() -> None` method, and QML roles `progressFraction` (float) and `watched` (bool). `EpisodeListModel` additionally gains `set_media_id(media_id: str) -> None` and a read-only `media_id` property.

- [ ] **Step 1: Write the failing test**

Append to `tests/presentation/test_models.py`:

```python
from PySide6.QtCore import Qt

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaItem, PlaybackProgress, Video
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.search_results_model import SearchResultsModel


class _Store:
    def __init__(self, entries: list[PlaybackProgress]) -> None:
        self.entries = entries

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None: ...
    def clear(self) -> None: ...


def _entry(media_id: str, video_id: str, **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "N",
        "poster": None,
        "label": "",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def test_episode_model_exposes_progress_roles(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([
        _entry("tt9", "tt9:1:1", type="series", position=150.0),
        _entry("tt9", "tt9:1:2", type="series", watched=True, position=0.0),
    ]))
    model = EpisodeListModel(repo)
    model.set_media_id("tt9")
    model.set_videos([
        Video(id="tt9:1:1", title="One", season=1, episode=1),
        Video(id="tt9:1:2", title="Two", season=1, episode=2),
        Video(id="tt9:1:3", title="Three", season=1, episode=3),
    ])
    frac = EpisodeListModel.ProgressFractionRole
    watched = EpisodeListModel.WatchedRole
    assert model.data(model.index(0, 0), frac) == 0.25
    assert model.data(model.index(1, 0), watched) is True
    assert model.data(model.index(2, 0), frac) == 0.0  # never started
    names = model.roleNames()
    assert names[frac] == b"progressFraction"
    assert names[watched] == b"watched"


def test_episode_model_without_a_repo_reports_zero(qapp: object) -> None:
    model = EpisodeListModel()
    model.set_videos([Video(id="v1", title="One", season=1, episode=1)])
    assert model.data(model.index(0, 0), EpisodeListModel.ProgressFractionRole) == 0.0


def test_poster_model_movie_reads_its_own_entry(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([_entry("tt1", "")]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressFractionRole) == 0.25


def test_poster_model_series_reads_the_latest_episode(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([
        _entry("tt9", "tt9:1:1", type="series", position=60.0, updated_at=100),
        _entry("tt9", "tt9:1:2", type="series", position=300.0, updated_at=200),
    ]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="S", poster=None)])
    assert model.data(model.index(0, 0), PosterGridModel.ProgressFractionRole) == 0.5


def test_poster_model_series_never_reports_watched(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([
        _entry("tt9", "tt9:1:1", type="series", watched=True, position=0.0),
    ]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt9", type="series", name="S", poster=None)])
    # One finished episode does not finish the show.
    assert model.data(model.index(0, 0), PosterGridModel.WatchedRole) is False


def test_refresh_progress_emits_datachanged_for_progress_roles(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([]))
    model = PosterGridModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    seen: list[list[int]] = []
    model.dataChanged.connect(lambda tl, br, roles: seen.append(list(roles)))
    model.refresh_progress()
    assert seen == [[PosterGridModel.ProgressFractionRole, PosterGridModel.WatchedRole]]


def test_refresh_progress_on_empty_model_is_a_noop(qapp: object) -> None:
    model = PosterGridModel(WatchProgressRepository(_Store([])))
    seen: list[object] = []
    model.dataChanged.connect(lambda *a: seen.append(a))
    model.refresh_progress()
    assert seen == []  # index(-1, 0) would be invalid


def test_search_results_model_exposes_progress_roles(qapp: object) -> None:
    repo = WatchProgressRepository(_Store([_entry("tt1", "")]))
    model = SearchResultsModel(repo)
    model.set_items([MediaItem(id="tt1", type="movie", name="M", poster=None)])
    assert model.data(model.index(0, 0), SearchResultsModel.ProgressFractionRole) == 0.25
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/presentation/test_models.py -v -k progress`
Expected: FAIL with `AttributeError: type object 'EpisodeListModel' has no attribute 'ProgressFractionRole'`

- [ ] **Step 3: Write the shared lookup**

`PosterGridModel` and `SearchResultsModel` both hold plain `MediaItem` lists and need the identical movie-vs-series rule, so it lives in one place rather than being copied into each.

Create `src/gravitas/presentation/models/progress_roles.py`:

```python
"""The progress lookup shared by every model that shows poster cards.

PosterGridModel and SearchResultsModel both hold MediaItems and need the same
movie-vs-series rule; keeping it here means the rule has one definition.
"""

from __future__ import annotations

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaItem


def fraction_for(progress: WatchProgressRepository | None, item: MediaItem) -> float:
    if progress is None:
        return 0.0
    if item.type == "series":
        # A series poster shows how far into the in-progress episode we are.
        entry = progress.latest_for(item.id)
        return entry.fraction if entry is not None else 0.0
    return progress.fraction_for(item.id)


def is_watched(progress: WatchProgressRepository | None, item: MediaItem) -> bool:
    # One finished episode does not finish a series, and a grid has no episode
    # count to judge by — so only movies ever badge as watched.
    if progress is None or item.type == "series":
        return False
    return progress.is_watched(item.id)
```

- [ ] **Step 4: Update EpisodeListModel**

In `src/gravitas/presentation/models/episode_list_model.py`, add the import and the roles:

```python
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import Video
```

```python
    ProgressFractionRole = Qt.ItemDataRole.UserRole + 8
    WatchedRole = Qt.ItemDataRole.UserRole + 9
```

Replace `__init__` and add the two methods:

```python
    def __init__(self, progress: WatchProgressRepository | None = None) -> None:
        super().__init__()
        self._videos: list[Video] = []
        self._progress = progress
        self._media_id = ""

    @property
    def media_id(self) -> str:
        return self._media_id

    def set_media_id(self, media_id: str) -> None:
        """The series these episodes belong to — progress is keyed by it."""
        self._media_id = media_id

    def refresh_progress(self) -> None:
        """Re-read the progress roles for every row (the underlying dict moved)."""
        if not self._videos:
            return
        self.dataChanged.emit(
            self.index(0, 0, _ROOT_INDEX),
            self.index(len(self._videos) - 1, 0, _ROOT_INDEX),
            [EpisodeListModel.ProgressFractionRole, EpisodeListModel.WatchedRole],
        )
```

Add to the `match role:` block in `data`:

```python
            case EpisodeListModel.ProgressFractionRole:
                if self._progress is None:
                    return 0.0
                return self._progress.fraction_for(self._media_id, video.id)
            case EpisodeListModel.WatchedRole:
                if self._progress is None:
                    return False
                return self._progress.is_watched(self._media_id, video.id)
```

Add to `roleNames`:

```python
            EpisodeListModel.ProgressFractionRole: QByteArray(b"progressFraction"),
            EpisodeListModel.WatchedRole: QByteArray(b"watched"),
```

- [ ] **Step 5: Update PosterGridModel**

In `src/gravitas/presentation/models/poster_grid_model.py`, add the imports:

```python
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.presentation.models import progress_roles
```

Add the roles:

```python
    ProgressFractionRole = Qt.ItemDataRole.UserRole + 7
    WatchedRole = Qt.ItemDataRole.UserRole + 8
```

Replace `__init__` and add `refresh_progress`:

```python
    def __init__(self, progress: WatchProgressRepository | None = None) -> None:
        super().__init__()
        self._items: list[MediaItem] = []
        self._progress = progress

    def refresh_progress(self) -> None:
        if not self._items:
            return
        self.dataChanged.emit(
            self.index(0, 0, _ROOT_INDEX),
            self.index(len(self._items) - 1, 0, _ROOT_INDEX),
            [PosterGridModel.ProgressFractionRole, PosterGridModel.WatchedRole],
        )
```

Add to `data`'s `match role:` block:

```python
            case PosterGridModel.ProgressFractionRole:
                return progress_roles.fraction_for(self._progress, item)
            case PosterGridModel.WatchedRole:
                return progress_roles.is_watched(self._progress, item)
```

Add to `roleNames`:

```python
            PosterGridModel.ProgressFractionRole: QByteArray(b"progressFraction"),
            PosterGridModel.WatchedRole: QByteArray(b"watched"),
```

- [ ] **Step 6: Update SearchResultsModel**

Apply the same treatment to `src/gravitas/presentation/models/search_results_model.py`, calling the same shared helper — do NOT copy the lookup rule in:

- add both imports (`WatchProgressRepository`, `progress_roles`);
- add `ProgressFractionRole = Qt.ItemDataRole.UserRole + 6` and `WatchedRole = Qt.ItemDataRole.UserRole + 7`;
- take `progress: WatchProgressRepository | None = None` as the first constructor arg, stored as `self._progress`;
- add a `refresh_progress` mirroring `PosterGridModel`'s, over whatever the file names its item list;
- add the two `case` arms, calling `progress_roles.fraction_for(self._progress, item)` and `progress_roles.is_watched(self._progress, item)`;
- add the two `roleNames` entries.

- [ ] **Step 7: Update CatalogRowsModel to propagate the repo**

In `src/gravitas/presentation/models/catalog_rows_model.py`, add the import, then replace `__init__` and `set_rows`, and add `refresh_progress`:

```python
    def __init__(self, progress: WatchProgressRepository | None = None) -> None:
        super().__init__()
        self._progress = progress
        self._all_rows: list[tuple[str, str, str, str, PosterGridModel]] = []
        self._rows: list[tuple[str, str, str, str, PosterGridModel]] = []
        self._filter = "all"

    def set_rows(self, rows: list[CatalogRow]) -> None:
        self.beginResetModel()
        built: list[tuple[str, str, str, str, PosterGridModel]] = []
        for row in rows:
            poster_model = PosterGridModel(self._progress)
            poster_model.set_items(row.items)
            built.append((row.title, row.addon_id, row.type, row.catalog_id, poster_model))
        self._all_rows = built
        self._rows = self._filtered(self._all_rows, self._filter)
        self.endResetModel()

    def refresh_progress(self) -> None:
        # The nested poster models own the visible cells; refresh every row's,
        # not just the filtered subset — a filter switch must not show stale bars.
        for row in self._all_rows:
            row[4].refresh_progress()
```

- [ ] **Step 8: Run tests and gates**

Run: `uv run pytest tests/presentation -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/gravitas/presentation/models tests/presentation/test_models.py
git commit -m "feat(models): progress bars via model roles"
```

---

### Task 8: WatchedListModel and ProgressController

**Files:**
- Create: `src/gravitas/presentation/models/watched_list_model.py`
- Create: `src/gravitas/presentation/controllers/progress_controller.py`
- Test: `tests/presentation/test_watched_list_model.py`
- Test: `tests/presentation/test_progress_controller.py`

**Interfaces:**
- Consumes: `WatchProgressRepository` (Task 3), `PlaybackProgress` (Task 1).
- Produces: `WatchedListModel()` with `set_entries(entries: list[PlaybackProgress]) -> None` and roles `mediaId`, `videoId`, `type`, `name`, `poster`, `label`, `progressFraction`. `ProgressController(progress, model)` with Property `revision: int`, slots `hasProgress(str) -> bool`, `isWatched(str, str) -> bool`, `forget(str, str)`, `forgetMedia(str)`, `markWatched(dict)`, `resetAll()`, `refreshWatched()`, `inProgressCount() -> int`, and signal `progressChanged()`.

- [ ] **Step 1: Write the failing model test**

Create `tests/presentation/test_watched_list_model.py`:

```python
from gravitas.domain.models import PlaybackProgress
from gravitas.presentation.models.watched_list_model import WatchedListModel


def entry(**kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": "tt9",
        "video_id": "tt9:1:2",
        "type": "series",
        "name": "The Show",
        "poster": "http://p/9.jpg",
        "label": "S1E2 · Two",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def test_rows_and_roles(qapp: object) -> None:
    model = WatchedListModel()
    model.set_entries([entry()])
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, WatchedListModel.MediaIdRole) == "tt9"
    assert model.data(index, WatchedListModel.VideoIdRole) == "tt9:1:2"
    assert model.data(index, WatchedListModel.TypeRole) == "series"
    assert model.data(index, WatchedListModel.NameRole) == "The Show"
    assert model.data(index, WatchedListModel.PosterRole) == "http://p/9.jpg"
    assert model.data(index, WatchedListModel.LabelRole) == "S1E2 · Two"
    assert model.data(index, WatchedListModel.ProgressFractionRole) == 0.25


def test_role_names(qapp: object) -> None:
    names = WatchedListModel().roleNames()
    assert names[WatchedListModel.MediaIdRole] == b"mediaId"
    assert names[WatchedListModel.ProgressFractionRole] == b"progressFraction"


def test_missing_poster_is_empty_string(qapp: object) -> None:
    model = WatchedListModel()
    model.set_entries([entry(poster=None)])
    assert model.data(model.index(0, 0), WatchedListModel.PosterRole) == ""


def test_set_entries_replaces(qapp: object) -> None:
    model = WatchedListModel()
    model.set_entries([entry()])
    model.set_entries([])
    assert model.rowCount() == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/presentation/test_watched_list_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gravitas.presentation.models.watched_list_model'`

- [ ] **Step 3: Write the model**

Create `src/gravitas/presentation/models/watched_list_model.py`:

```python
"""Qt list model exposing in-progress titles to the Settings page.

One row per media (the latest episode for a series), newest first — the list
is a management surface, not a history log.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
)

from gravitas.domain.models import PlaybackProgress

_ROOT_INDEX = QModelIndex()


class WatchedListModel(QAbstractListModel):
    MediaIdRole = Qt.ItemDataRole.UserRole + 1
    VideoIdRole = Qt.ItemDataRole.UserRole + 2
    TypeRole = Qt.ItemDataRole.UserRole + 3
    NameRole = Qt.ItemDataRole.UserRole + 4
    PosterRole = Qt.ItemDataRole.UserRole + 5
    LabelRole = Qt.ItemDataRole.UserRole + 6
    ProgressFractionRole = Qt.ItemDataRole.UserRole + 7

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[PlaybackProgress] = []

    def set_entries(self, entries: list[PlaybackProgress]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return len(self._entries)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid():
            return None
        entry = self._entries[index.row()]
        match role:
            case WatchedListModel.MediaIdRole:
                return entry.media_id
            case WatchedListModel.VideoIdRole:
                return entry.video_id
            case WatchedListModel.TypeRole:
                return entry.type
            case WatchedListModel.NameRole:
                return entry.name
            case WatchedListModel.PosterRole:
                return entry.poster or ""
            case WatchedListModel.LabelRole:
                return entry.label
            case WatchedListModel.ProgressFractionRole:
                return entry.fraction
        return None

    def roleNames(self) -> dict[int, QByteArray]:
        return {
            WatchedListModel.MediaIdRole: QByteArray(b"mediaId"),
            WatchedListModel.VideoIdRole: QByteArray(b"videoId"),
            WatchedListModel.TypeRole: QByteArray(b"type"),
            WatchedListModel.NameRole: QByteArray(b"name"),
            WatchedListModel.PosterRole: QByteArray(b"poster"),
            WatchedListModel.LabelRole: QByteArray(b"label"),
            WatchedListModel.ProgressFractionRole: QByteArray(b"progressFraction"),
        }
```

- [ ] **Step 4: Write the failing controller test**

Create `tests/presentation/test_progress_controller.py`:

```python
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import PlaybackProgress
from gravitas.presentation.controllers.progress_controller import ProgressController
from gravitas.presentation.models.watched_list_model import WatchedListModel


class _Store:
    def __init__(self, entries: list[PlaybackProgress]) -> None:
        self.entries = entries
        self.deleted: list[tuple[str, str | None]] = []
        self.cleared = False

    def load_all(self) -> list[PlaybackProgress]:
        return list(self.entries)

    def save(self, entry: PlaybackProgress) -> None: ...

    def delete(self, media_id: str, video_id: str | None = None) -> None:
        self.deleted.append((media_id, video_id))

    def clear(self) -> None:
        self.cleared = True


def entry(media_id: str = "tt1", video_id: str = "", **kw: object) -> PlaybackProgress:
    base: dict[str, object] = {
        "media_id": media_id,
        "video_id": video_id,
        "type": "movie",
        "name": "M",
        "poster": None,
        "label": "",
        "position": 150.0,
        "duration": 600.0,
        "watched": False,
        "updated_at": 100,
    }
    base.update(kw)
    return PlaybackProgress(**base)  # type: ignore[arg-type]


def build(entries: list[PlaybackProgress]) -> tuple[ProgressController, _Store, WatchedListModel]:
    store = _Store(entries)
    model = WatchedListModel()
    return ProgressController(WatchProgressRepository(store), model), store, model


def test_has_progress(qapp: object) -> None:
    controller, _, _ = build([entry()])
    assert controller.hasProgress("tt1") is True
    assert controller.hasProgress("tt2") is False


def test_forget_one_video_delegates_and_signals(qapp: object) -> None:
    controller, store, _ = build([entry("tt9", "tt9:1:1", type="series")])
    fired: list[None] = []
    controller.progressChanged.connect(lambda: fired.append(None))
    controller.forget("tt9", "tt9:1:1")
    assert store.deleted == [("tt9", "tt9:1:1")]
    assert fired == [None]


def test_forget_media_delegates(qapp: object) -> None:
    controller, store, _ = build([entry("tt9", "tt9:1:1", type="series")])
    controller.forgetMedia("tt9")
    assert store.deleted == [("tt9", None)]


def test_reset_all_delegates(qapp: object) -> None:
    controller, store, _ = build([entry()])
    controller.resetAll()
    assert store.cleared is True
    assert controller.inProgressCount() == 0


def test_revision_bumps_on_every_mutation(qapp: object) -> None:
    controller, _, _ = build([entry()])
    before = controller.revision
    controller.forgetMedia("tt1")
    assert controller.revision == before + 1


def test_mutations_refresh_the_settings_model(qapp: object) -> None:
    controller, _, model = build([entry()])
    controller.refreshWatched()
    assert model.rowCount() == 1
    controller.forgetMedia("tt1")
    assert model.rowCount() == 0  # the list must not keep a forgotten row


def test_mark_watched_from_a_context_map(qapp: object) -> None:
    controller, _, _ = build([])
    controller.markWatched({
        "mediaId": "tt9",
        "videoId": "tt9:1:1",
        "type": "series",
        "name": "Show",
        "poster": "",
        "label": "S1E1",
    })
    assert controller.isWatched("tt9", "tt9:1:1") is True


def test_mark_watched_without_a_media_id_is_ignored(qapp: object) -> None:
    controller, _, _ = build([])
    controller.markWatched({"mediaId": "", "videoId": "", "type": "movie"})
    assert controller.inProgressCount() == 0
```

- [ ] **Step 5: Run it to verify it fails**

Run: `uv run pytest tests/presentation/test_progress_controller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gravitas.presentation.controllers.progress_controller'`

- [ ] **Step 6: Write the controller**

Create `src/gravitas/presentation/controllers/progress_controller.py`:

```python
"""QObject bridge: forget/reset watch progress and feed the Settings list."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.models import MediaType
from gravitas.presentation.models.watched_list_model import WatchedListModel


class ProgressController(QObject):
    progressChanged = Signal()

    def __init__(self, progress: WatchProgressRepository, model: WatchedListModel) -> None:
        super().__init__()
        self._progress = progress
        self._model = model
        self._revision = 0

    @Property(int, notify=progressChanged)
    def revision(self) -> int:
        """Bumped on every mutation. A QML binding that calls hasProgress()
        must read `revision` too — a Slot call is not a reactive dependency,
        so without it the binding never re-evaluates when progress changes."""
        return self._revision

    # --- queries ---

    @Slot(str, result=bool)
    def hasProgress(self, media_id: str) -> bool:
        return self._progress.latest_for(media_id) is not None

    @Slot(str, str, result=bool)
    def isWatched(self, media_id: str, video_id: str) -> bool:
        return self._progress.is_watched(media_id, video_id)

    @Slot(result=int)
    def inProgressCount(self) -> int:
        return len(self._progress.in_progress())

    # --- mutations ---

    @Slot(str, str)
    def forget(self, media_id: str, video_id: str) -> None:
        self._progress.forget(media_id, video_id)
        self._changed()

    @Slot(str)
    def forgetMedia(self, media_id: str) -> None:
        self._progress.forget(media_id)
        self._changed()

    @Slot("QVariantMap")
    def markWatched(self, context: dict[str, object]) -> None:
        media_id = str(context.get("mediaId", ""))
        if not media_id:
            return
        media_type: MediaType = "series" if context.get("type") == "series" else "movie"
        poster = context.get("poster")
        self._progress.mark_watched(
            media_id=media_id,
            video_id=str(context.get("videoId", "")),
            type=media_type,
            name=str(context.get("name", "")),
            poster=str(poster) if poster else None,
            label=str(context.get("label", "")),
        )
        self._changed()

    @Slot()
    def resetAll(self) -> None:
        self._progress.reset_all()
        self._changed()

    @Slot()
    def refreshWatched(self) -> None:
        """Repopulate the Settings list (called when the page opens)."""
        self._model.set_entries(self._progress.in_progress())

    def _changed(self) -> None:
        self._revision += 1
        self._model.set_entries(self._progress.in_progress())
        self.progressChanged.emit()
```

- [ ] **Step 7: Run tests and gates**

Run: `uv run pytest tests/presentation -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/gravitas/presentation/models/watched_list_model.py src/gravitas/presentation/controllers/progress_controller.py tests/presentation/test_watched_list_model.py tests/presentation/test_progress_controller.py
git commit -m "feat(progress): controller and Settings list model"
```

---

### Task 9: Composition root wiring

**Files:**
- Modify: `src/gravitas/main.py`
- Test: `tests/test_composition.py`

**Interfaces:**
- Consumes: everything from Tasks 2, 3, 5, 7, 8.
- Produces: QML context properties `progressController` and `watchedListModel`; every model's bars refresh on `progressChanged` / `progressRecorded`; progress flushed on `aboutToQuit`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_composition.py`. Mirror the existing test's loop scaffolding — `build_app` stays asyncio-free but the file sets a loop up around it, and `assert engine.rootObjects()` is what catches a QML parse error:

```python
def test_build_app_wires_watch_progress(qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # Never touch the developer's real progress database from a test.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
        ctx = engine.rootContext()
        assert ctx.contextProperty("progressController") is not None
        assert ctx.contextProperty("watchedListModel") is not None
        # setContextProperty does not take ownership; without a surviving
        # Python reference these read back as null in QML.
        names = {type(ref).__name__ for ref in engine._gravitas_refs}
        assert "ProgressController" in names
        assert "WatchedListModel" in names
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
```

Add the imports the file lacks: `from pathlib import Path` and `from pytest import MonkeyPatch`.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_composition.py -v -k watch_progress`
Expected: FAIL — `contextProperty("progressController")` is None.

- [ ] **Step 3: Wire it up**

In `src/gravitas/main.py`, add the imports:

```python
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.infrastructure.progress.sqlite_store import SqliteProgressStore
from gravitas.presentation.controllers.progress_controller import ProgressController
from gravitas.presentation.models.watched_list_model import WatchedListModel
```

Build the repo right after `settings_store`/`persisted` (it must exist before any model that takes it):

```python
    progress_repo = WatchProgressRepository(SqliteProgressStore())
```

Pass it to the models — replace the existing constructions:

```python
    rows_model = CatalogRowsModel(progress_repo)
    stream_model = StreamListModel()

    discover_model = PosterGridModel(progress_repo)
    discover_proxy = PosterGridProxy(discover_model)
    discover_controller = DiscoverController(BrowseBoard(repo), repo, discover_model)

    catalog_controller = CatalogController(BrowseCatalog(repo), rows_model)
    episode_model = EpisodeListModel(progress_repo)
```

```python
    search_results_model = SearchResultsModel(progress_repo)
    search_page_model = SearchResultsModel(progress_repo)
```

Build the progress controller and pass the repo to the player, replacing the `player_controller = ...` line:

```python
    watched_model = WatchedListModel()
    progress_controller = ProgressController(progress_repo, watched_model)

    player_controller = PlayerController(make_player, lambda: sub_style.style, progress_repo)
    # Live-apply subtitle style edits to an active player.
    settings_controller.subtitleStyleChanged.connect(player_controller.applySubtitleStyle)

    # Bars are model roles, so every surface showing progress must re-read them
    # when the underlying index moves — whether the player advanced it or the
    # user forgot something.
    def _refresh_progress_bars() -> None:
        rows_model.refresh_progress()
        discover_model.refresh_progress()
        episode_model.refresh_progress()
        search_results_model.refresh_progress()
        search_page_model.refresh_progress()

    progress_controller.progressChanged.connect(_refresh_progress_bars)
    player_controller.progressRecorded.connect(_refresh_progress_bars)
    # The final seconds of a session would otherwise die with the process.
    app.aboutToQuit.connect(player_controller.flushProgress)
```

Add the context properties next to the others:

```python
    ctx.setContextProperty("progressController", progress_controller)
    ctx.setContextProperty("watchedListModel", watched_model)
```

Add both to the keep-alive tuple in `engine._gravitas_refs`:

```python
        progress_controller,
        watched_model,
```

- [ ] **Step 4: Bind the episode model to its series**

Append to `tests/presentation/test_detail_controller.py`:

```python
async def test_episode_model_is_bound_to_the_series(qapp: object) -> None:
    ctl, _stream_model, episode_model = _series_ctl()
    await ctl.load("series", "tt1")
    # Episode progress is keyed by (media_id, video_id). Without this the model
    # looks every episode up under an empty media id and every bar reads zero.
    assert episode_model.media_id == "tt1"
```

Then in `src/gravitas/presentation/controllers/detail_controller.py`, in `load`, replace the existing two-line block:

```python
        if self._episode_model is not None:
            self._episode_model.set_videos([])
```

with:

```python
        if self._episode_model is not None:
            self._episode_model.set_media_id(item_id)
            self._episode_model.set_videos([])
```

Run: `uv run pytest tests/presentation/test_detail_controller.py -v -k bound`
Expected: PASS.

- [ ] **Step 5: Run the full suite and gates**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/main.py src/gravitas/presentation/controllers/detail_controller.py tests/test_composition.py
git commit -m "feat(progress): wire progress into the composition root"
```

---

### Task 10: QML primitives — check glyph, ContextMenu, ConfirmDialog

**Files:**
- Modify: `src/gravitas/presentation/qml/components/Icons.qml`
- Create: `src/gravitas/presentation/qml/components/ContextMenu.qml`
- Create: `src/gravitas/presentation/qml/components/ConfirmDialog.qml`

**Interfaces:**
- Consumes: `Theme`, `AppButton`, `AppScrollBar` (existing).
- Produces: `Icons.check`; `ContextMenu` with `property var entries` (list of `{label, action}` where `action` is a JS function) and `function popupAt(item, position)`; `ConfirmDialog` with `property string heading`, `property string body`, `property string confirmText`, `signal confirmed()`, and `function ask()`.

QML has no unit tests — it is verified at launch (Task 13's final step).

- [ ] **Step 1: Add the glyph**

In `src/gravitas/presentation/qml/components/Icons.qml`, add next to the other glyph properties:

```qml
    readonly property string check: String.fromCharCode(0xe5ca) // check
```

- [ ] **Step 2: Write ContextMenu.qml**

Create `src/gravitas/presentation/qml/components/ContextMenu.qml`:

```qml
import QtQuick
import QtQuick.Controls
import "."

// Right-click / long-press menu. The project builds its own controls rather
// than using stock QtQuick.Controls Menu (which ignores our styling on some
// platform styles), so this follows TrackMenu's themed-Popup pattern.
// `entries` is a list of { label, action } — action is a JS function.
Popup {
    id: menu
    property var entries: []

    // Opens with its top-left at the cursor, clamped inside the window.
    function popupAt(item, position) {
        var point = item.mapToItem(null, position.x, position.y)
        var w = menu.width
        var h = menu.implicitHeight
        var maxX = (Window.window ? Window.window.width : point.x + w) - w - 8
        var maxY = (Window.window ? Window.window.height : point.y + h) - h - 8
        menu.parent = Window.window ? Window.window.contentItem : item
        menu.x = Math.max(8, Math.min(point.x, maxX))
        menu.y = Math.max(8, Math.min(point.y, maxY))
        menu.open()
    }

    modal: true
    dim: false
    padding: 4
    width: Math.min(320, Math.max(180, contentNeed))

    // Imperative measure — a binding that writes TextMetrics.text and reads
    // its width would retrigger itself (same reasoning as TrackMenu).
    property real contentNeed: 180
    TextMetrics { id: entryMetrics; font.pixelSize: Theme.fontBody }
    onEntriesChanged: {
        var longest = 0
        for (var i = 0; i < entries.length; i++) {
            entryMetrics.text = entries[i].label
            longest = Math.max(longest, entryMetrics.advanceWidth)
        }
        contentNeed = longest + Theme.spacing * 3
    }

    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
    }

    contentItem: Column {
        spacing: 2
        Repeater {
            model: menu.entries
            Rectangle {
                required property var modelData
                width: menu.width - 8
                height: Theme.controlHeight
                radius: Theme.radiusSmall
                color: rowHover.hovered ? Theme.surfaceHover : "transparent"
                HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        menu.close()
                        modelData.action()
                    }
                }
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.spacing
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.spacing
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.label
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    elide: Text.ElideRight
                }
            }
        }
    }
}
```

- [ ] **Step 3: Write ConfirmDialog.qml**

Create `src/gravitas/presentation/qml/components/ConfirmDialog.qml`:

```qml
import QtQuick
import QtQuick.Controls
import "."

// Modal confirm for destructive actions. `ask()` opens it centred on the
// window; the confirm button emits confirmed().
Popup {
    id: dialog
    property string heading: ""
    property string body: ""
    property string confirmText: "Delete"
    signal confirmed()

    function ask() {
        dialog.parent = Window.window ? Window.window.contentItem : dialog.parent
        dialog.open()
    }

    anchors.centerIn: Overlay.overlay
    modal: true
    dim: true
    padding: 24
    width: Math.min(420, Window.window ? Window.window.width - 48 : 420)
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        radius: Theme.radius * 1.5
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
    }

    contentItem: Column {
        spacing: 16
        Text {
            width: parent.width
            text: dialog.heading
            color: Theme.text
            font.pixelSize: Theme.fontTitle
            font.bold: true
            wrapMode: Text.WordWrap
        }
        Text {
            width: parent.width
            visible: text.length > 0
            text: dialog.body
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }
        Row {
            anchors.right: parent.right
            spacing: 8
            AppButton {
                text: "Cancel"
                ghost: true
                onClicked: dialog.close()
            }
            AppButton {
                text: dialog.confirmText
                tone: "negative"
                onClicked: {
                    dialog.close()
                    dialog.confirmed()
                }
            }
        }
    }
}
```

- [ ] **Step 4: Verify the components parse**

Run: `uv run gravitas`
Expected: the app launches with no QML errors on stderr. (Nothing uses the new components yet — this only proves they compile.) Close it.

If no display is available, run `QT_QPA_PLATFORM=offscreen uv run gravitas` instead and confirm the same.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/components/Icons.qml src/gravitas/presentation/qml/components/ContextMenu.qml src/gravitas/presentation/qml/components/ConfirmDialog.qml
git commit -m "feat(ui): context menu and confirm dialog primitives"
```

---

### Task 11: Progress bars and context menus on rows and cards

**Files:**
- Modify: `src/gravitas/presentation/qml/components/EpisodeRow.qml`
- Modify: `src/gravitas/presentation/qml/components/PosterCard.qml`
- Modify: `src/gravitas/presentation/qml/Detail.qml:211-231`
- Modify: `src/gravitas/presentation/qml/Discover.qml`
- Modify: `src/gravitas/presentation/qml/SearchResults.qml`
- Modify: `src/gravitas/presentation/qml/components/CatalogRowStrip.qml`

**Interfaces:**
- Consumes: roles `progressFraction` / `watched` (Task 7), `progressController` (Tasks 8–9), `ContextMenu` (Task 10).
- Produces: `EpisodeRow` and `PosterCard` each gain `property real progressFraction: 0`, `property bool watched: false`, `property var forgetContext: null` (a `{mediaId, videoId, type, name, poster, label}` map; when null, no menu opens).

- [ ] **Step 1: Add the bar and menu to EpisodeRow**

In `src/gravitas/presentation/qml/components/EpisodeRow.qml`, add to the property block:

```qml
    property real progressFraction: 0
    property bool watched: false
    // { mediaId, videoId, type, name, poster, label } — null disables the menu.
    property var forgetContext: null
```

Replace the existing `TapHandler { onTapped: root.clicked() }` with:

```qml
    TapHandler { onTapped: root.clicked() }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: (event) => root.openMenu(event.position)
    }
    TapHandler {
        // Touch/trackpad equivalent of a right-click.
        acceptedDevices: PointerDevice.TouchScreen
        onLongPressed: root.openMenu(point.position)
    }

    function openMenu(position) {
        if (!root.forgetContext)
            return
        var items = []
        if (!root.watched) {
            items.push({
                label: "Mark as watched",
                action: () => progressController.markWatched(root.forgetContext)
            })
        }
        if (root.progressFraction > 0 || root.watched) {
            items.push({
                label: "Forget progress",
                action: () => progressController.forget(
                    root.forgetContext.mediaId, root.forgetContext.videoId)
            })
        }
        if (items.length === 0)
            return
        rowMenu.entries = items
        rowMenu.popupAt(root, position)
    }

    ContextMenu { id: rowMenu }
```

Add the bar as the last child of the root Rectangle (after the `Row { ... }` block), plus a checkmark in the episode-number row:

```qml
    // Resume bar, pinned to the row's bottom edge inside its rounded corners.
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 2
        height: 3
        radius: 1.5
        color: Theme.border
        visible: root.progressFraction > 0 && !root.watched
        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: parent.width * Math.max(0, Math.min(1, root.progressFraction))
            radius: 1.5
            color: Theme.accent
            Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
        }
    }
```

In the `Row` holding `"E" + root.episodeNumber` and the title, append a check glyph after the title `Text` and narrow the title to make room:

```qml
                AppIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.watched
                    glyph: Icons.check
                    font.pixelSize: 16
                    color: Theme.positive
                }
```

and change the title `Text`'s width from `parent.width - 90` to `parent.width - 90 - (root.watched ? 24 : 0)`. Dim a watched row's title by changing its `color:` to `root.watched ? Theme.textDim : Theme.text`.

- [ ] **Step 2: Add the bar and menu to PosterCard**

In `src/gravitas/presentation/qml/components/PosterCard.qml`, add to the property block:

```qml
    property real progressFraction: 0
    property bool watched: false
    // { mediaId, videoId, type, name, poster, label } — null disables the menu.
    property var forgetContext: null
```

The card uses a `MouseArea` (not handlers), so extend it — replace the existing `MouseArea { id: mouse ... }` with:

```qml
            MouseArea {
                id: mouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onClicked: (event) => {
                    if (event.button === Qt.RightButton)
                        root.openMenu(Qt.point(event.x, event.y))
                    else
                        root.clicked()
                }
                onPressAndHold: (event) => root.openMenu(Qt.point(event.x, event.y))
            }
```

Add the function and menu at the root level (after the `signal clicked()` line):

```qml
    function openMenu(position) {
        if (!root.forgetContext)
            return
        var items = []
        // Movies only. A series poster can never show a watched badge (the
        // model reports watched=false for series), and marking one watched
        // here would write a videoId-less, zero-duration entry that becomes
        // the series' latest_for() — silently hiding real episode progress
        // with no badge, no feedback, and no undo. Episodes are marked
        // watched from the Detail page, where the episodes actually are.
        if (!root.watched && root.forgetContext.type !== "series") {
            items.push({
                label: "Mark as watched",
                action: () => progressController.markWatched(root.forgetContext)
            })
        }
        if (root.progressFraction > 0 || root.watched) {
            items.push({
                label: "Forget progress",
                action: () => progressController.forgetMedia(root.forgetContext.mediaId)
            })
        }
        if (items.length === 0)
            return
        cardMenu.entries = items
        cardMenu.popupAt(root, position)
    }

    ContextMenu { id: cardMenu }
```

Inside `Item { id: cover ... }`, after the hover frame Rectangle and **before** the `MouseArea`, add the bar and the watched badge:

```qml
            // Resume bar across the poster's bottom edge, inside the rounding.
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 6
                height: 4
                radius: 2
                color: Qt.rgba(0, 0, 0, 0.55)
                visible: root.progressFraction > 0 && !root.watched
                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: parent.width * Math.max(0, Math.min(1, root.progressFraction))
                    radius: 2
                    color: Theme.accent
                    Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
                }
            }

            Rectangle {
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.margins: 8
                width: 24; height: 24; radius: 12
                color: Qt.rgba(0, 0, 0, 0.6)
                visible: root.watched
                AppIcon {
                    anchors.centerIn: parent
                    glyph: Icons.check
                    font.pixelSize: 15
                    color: Theme.positive
                }
            }
```

- [ ] **Step 3: Feed the roles from the episode delegate**

In `src/gravitas/presentation/qml/Detail.qml`, in the `EpisodeRow` delegate inside `Repeater { id: episodesRep ... }`, add:

```qml
                            progressFraction: model.progressFraction
                            watched: model.watched
                            forgetContext: ({
                                mediaId: detail.mediaId,
                                videoId: model.videoId,
                                type: "series",
                                name: detailController ? detailController.title : "",
                                poster: detailController ? detailController.poster : "",
                                label: "S" + model.season + "E" + model.episode
                                    + (model.title ? " · " + model.title : "")
                            })
```

- [ ] **Step 4: Feed the roles from the poster delegates**

In each of `Discover.qml`, `SearchResults.qml`, and `components/CatalogRowStrip.qml`, add to the `PosterCard` delegate:

```qml
            progressFraction: model.progressFraction
            watched: model.watched
            forgetContext: ({
                mediaId: model.id,
                videoId: "",
                type: model.type,
                name: model.name,
                poster: model.poster ? model.poster : "",
                label: ""
            })
```

`SearchResults.qml` uses `SearchResultsModel`, whose id role is named `mediaId`, not `id` — use `model.mediaId` there. Read each delegate before editing and match the role names it already uses.

- [ ] **Step 5: Verify at launch**

Run: `uv run gravitas`
Expected: no QML errors. Play something past 30 seconds, quit the player, and confirm a bar appears on the episode row / poster. Right-click a card and confirm the menu opens and "Forget progress" clears the bar.

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/presentation/qml
git commit -m "feat(ui): progress bars and forget menus on rows and cards"
```

---

### Task 12: Play with context, resume toast, and the Detail forget button

**Files:**
- Modify: `src/gravitas/presentation/qml/Detail.qml`
- Modify: `src/gravitas/presentation/qml/Sources.qml`
- Modify: `src/gravitas/presentation/qml/Player.qml`

**Interfaces:**
- Consumes: `detailController.mediaContext()` (Task 6), `playerController.setMediaContext` / `resumed` (Task 5), `progressController.forgetMedia` / `revision` / `hasProgress` (Task 8), `ConfirmDialog` (Task 10).
- Produces: nothing downstream.

- [ ] **Step 1: Set the context before playing**

In `src/gravitas/presentation/qml/Detail.qml`, replace the inline-sources delegate's click handler:

```qml
                        onClicked: {
                            if (!model.url)
                                return
                            // Identity must land before play(); the controller
                            // reads it to resume and to record.
                            playerController.setMediaContext(detailController.mediaContext())
                            detail.playUrl(model.url)
                        }
```

In `src/gravitas/presentation/qml/Sources.qml`, replace the delegate's handler with the same three lines (`sources.playUrl(model.url)` as the last one). `detailController.mediaContext()` already carries the selected episode, because `selectEpisode` ran before this page was pushed.

- [ ] **Step 2: Add the Detail forget button**

In `src/gravitas/presentation/qml/Detail.qml`, add inside the `Column { id: content ... }`, immediately after the meta `Row` (the runtime · year · rating one):

```qml
            AppButton {
                text: "Forget progress"
                ghost: true
                iconGlyph: Icons.trash
                tone: "negative"
                // hasProgress() is a Slot, not a binding dependency — reading
                // `revision` is what makes this re-evaluate when it changes.
                visible: progressController
                    && progressController.revision >= 0
                    && progressController.hasProgress(detail.mediaId)
                onClicked: forgetDialog.ask()
            }

            ConfirmDialog {
                id: forgetDialog
                heading: "Forget progress for "
                    + (detailController ? detailController.title : "this title") + "?"
                body: detail.mediaType === "series"
                    ? "Every episode's saved position is cleared. This cannot be undone."
                    : "The saved position is cleared. This cannot be undone."
                confirmText: "Forget"
                onConfirmed: progressController.forgetMedia(detail.mediaId)
            }
```

- [ ] **Step 3: Add the resume toast**

`Toast` lives in `Main.qml`, and `Player` has no reference to it, so surface the message from the player page instead — in `src/gravitas/presentation/qml/Player.qml`, add near the other `Connections`/top-level blocks:

```qml
    function formatClock(seconds) {
        var total = Math.max(0, Math.floor(seconds))
        var h = Math.floor(total / 3600)
        var m = Math.floor((total % 3600) / 60)
        var s = total % 60
        var mm = (h > 0 && m < 10 ? "0" : "") + m
        return (h > 0 ? h + ":" : "") + mm + ":" + (s < 10 ? "0" : "") + s
    }

    // Self-contained pill: the shared Toast lives in Main.qml and the player
    // page has no handle on it.
    Rectangle {
        id: resumePill
        z: 50
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 24
        width: resumeLabel.implicitWidth + 28
        height: 40
        radius: 20
        color: Qt.rgba(0, 0, 0, 0.78)
        border.width: 1
        border.color: Theme.borderStrong
        opacity: 0
        visible: opacity > 0
        Text {
            id: resumeLabel
            anchors.centerIn: parent
            color: Theme.text
            font.pixelSize: Theme.fontSmall
        }
        SequentialAnimation {
            id: resumeFade
            NumberAnimation { target: resumePill; property: "opacity"; to: 1; duration: Theme.durMed }
            PauseAnimation { duration: 2600 }
            NumberAnimation { target: resumePill; property: "opacity"; to: 0; duration: Theme.durMed }
        }
    }

    Connections {
        target: playerController
        function onResumed(position) {
            resumeLabel.text = "Resumed from " + player.formatClock(position)
            resumeFade.restart()
        }
    }
```

- [ ] **Step 4: Verify at launch**

Run: `uv run gravitas`
Expected: play a title past 30 seconds, leave the player, play it again — it resumes and the pill reads "Resumed from 0:31". The Detail page shows "Forget progress"; clicking it asks first, and confirming makes the button and the bars disappear.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/qml/Detail.qml src/gravitas/presentation/qml/Sources.qml src/gravitas/presentation/qml/Player.qml
git commit -m "feat(ui): auto-resume with a toast and a Detail forget action"
```

---

### Task 13: Settings — watch progress card and reset all

**Files:**
- Modify: `src/gravitas/presentation/qml/Settings.qml`

**Interfaces:**
- Consumes: `watchedListModel` (Tasks 8–9), `progressController.refreshWatched` / `forgetMedia` / `resetAll` / `inProgressCount` / `revision`, `ConfirmDialog` (Task 10).
- Produces: nothing downstream.

- [ ] **Step 1: Add the card**

In `src/gravitas/presentation/qml/Settings.qml`, insert between the "Subtitles" and "About" cards, and bump the About card's `enterDelay` from `120` to `150`:

```qml
            SettingsCard {
                width: parent.width
                title: "Watch progress"
                caption: "Saved playback positions, kept on this device only"
                enterDelay: 120

                // The list is only correct while the page is open; repopulate
                // on entry rather than keeping it live for a hidden page.
                Component.onCompleted: progressController.refreshWatched()

                Text {
                    visible: watchedList.count === 0
                    text: "Nothing in progress yet. Positions are saved automatically after 30 seconds of playback."
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                    width: parent.width
                }

                ListView {
                    id: watchedList
                    width: parent.width
                    height: Math.min(contentHeight, 320)
                    visible: count > 0
                    model: watchedListModel
                    interactive: contentHeight > height
                    clip: true
                    spacing: 6
                    ScrollBar.vertical: AppScrollBar {}
                    remove: Transition {
                        NumberAnimation { property: "opacity"; to: 0; duration: Theme.durFast }
                    }
                    displaced: Transition {
                        NumberAnimation { property: "y"; duration: Theme.durMed; easing.type: Easing.OutCubic }
                    }
                    delegate: Rectangle {
                        id: watchedRow
                        width: ListView.view.width
                        height: 56
                        radius: Theme.radius
                        color: watchedHover.hovered ? Theme.surfaceHover : Theme.bg
                        Behavior on color { ColorAnimation { duration: Theme.durFast } }
                        required property string mediaId
                        required property string name
                        required property string poster
                        required property string label
                        required property real progressFraction
                        HoverHandler { id: watchedHover }

                        Row {
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            anchors.right: forgetButton.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 12

                            Item {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 28; height: 40
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 4
                                    color: Theme.surfaceHover
                                    visible: thumb.status !== Image.Ready
                                }
                                Image {
                                    id: thumb
                                    anchors.fill: parent
                                    source: Img.sized(watchedRow.poster, 80)
                                    sourceSize.width: 80
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true
                                }
                            }
                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 2
                                width: parent.width - 40
                                Text {
                                    width: parent.width
                                    text: watchedRow.name
                                    color: Theme.text
                                    font.pixelSize: Theme.fontBody
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: parent.width
                                    text: (watchedRow.label.length > 0 ? watchedRow.label + " · " : "")
                                        + Math.round(watchedRow.progressFraction * 100) + "%"
                                    color: Theme.textDim
                                    font.pixelSize: Theme.fontSmall
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        // Fires immediately: it forgets one title, and watching
                        // it again puts it straight back.
                        AppButton {
                            id: forgetButton
                            ghost: true
                            iconGlyph: Icons.trash
                            tooltip: "Forget progress"
                            tone: "negative"
                            anchors.right: parent.right
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: progressController.forgetMedia(watchedRow.mediaId)
                        }
                    }
                }

                AppButton {
                    text: "Reset all progress"
                    tone: "negative"
                    visible: watchedList.count > 0
                    onClicked: {
                        resetDialog.count = progressController.inProgressCount()
                        resetDialog.ask()
                    }
                }

                ConfirmDialog {
                    id: resetDialog
                    property int count: 0
                    heading: "Forget progress for " + count
                        + (count === 1 ? " title?" : " titles?")
                    body: "Every saved position is cleared, including finished ones. This cannot be undone."
                    confirmText: "Reset all"
                    onConfirmed: progressController.resetAll()
                }
            }
```

- [ ] **Step 2: Verify at launch**

Run: `uv run gravitas`
Expected: no QML errors. With something in progress, Settings lists it with a poster, `S1E2 · 25%`, and a trash button that removes the row. "Reset all progress" asks for confirmation naming the count, then empties the list and clears every bar across Home, Discover, and Detail.

- [ ] **Step 3: Run the full suite and gates**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/gravitas/presentation/qml/Settings.qml
git commit -m "feat(settings): watch progress list and reset all"
```

---

## Self-Review Notes

Recorded during the plan's own review pass, for the implementer:

- **QML is not entirely untested.** `tests/test_composition.py` asserts `engine.rootObjects()` after loading `Main.qml`, so a QML parse or type error in any file it reaches fails the suite. Behaviour still needs a real launch (`uv run gravitas`), which is why Tasks 10–13 each end with one.
- **`AppIcon` in `EpisodeRow`/`PosterCard`** — both files already `import "."`, and both already use `AppIcon` and `Icons`. No new imports needed; `ContextMenu` and `ConfirmDialog` resolve the same way (directory import, not `qmldir`).
- **Series never badge as watched** (Task 7) — a deliberate narrowing of the spec's "watched → check badge", which only makes sense for movies: the grid has no episode count, so a series can never be known-finished there. Bars still work for both.
- **`Window.window` in `ContextMenu`/`ConfirmDialog`** requires `import QtQuick` (present) — `Window` is an attached property available to any Item.
- **The `revision` idiom** (Tasks 8, 12) is load-bearing and easy to "clean up" by mistake. A `@Slot` call inside a QML binding registers no dependency, so `visible: progressController.hasProgress(id)` alone would go stale forever. Do not remove the `revision >= 0` term.
- **Task 11's poster delegates are the one place still needing a read-first.** `Discover.qml`, `SearchResults.qml`, and `CatalogRowStrip.qml` each bind `PosterCard` against a different model, and `SearchResultsModel` names its id role `mediaId` where `PosterGridModel` names it `id`. Match each delegate's existing role names rather than pasting one version three times.
- **Two narrow accessors exist for testability**, by explicit decision: `PlayerController.is_recording()` (Task 5) and `EpisodeListModel.media_id` (Task 7). Both assert behaviour with no other public witness — a timer that never stops leaks writes after playback, and a wrong media id silently zeroes every episode bar. Neither is a Slot; QML does not use them. Do not replace them with private access in tests, and do not widen them.
- **`progress_roles.py` is deliberately shared** (Task 7) rather than copied into each poster model. The movie-vs-series rule has exactly one definition; if the series rule changes, it changes in one file.
