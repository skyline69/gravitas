# Watch Progress — Design

Date: 2026-07-15

## Goal

Persist playback position locally so a movie or episode resumes where the user
left off, show that progress in the UI, and let the user forget it — one item at
a time, one title at a time, or all of it.

Reads must be free (they happen per grid cell, on every frame of a flick) and
writes must be cheap (they happen every few seconds during playback).

## Scope

In scope:

- A SQLite-backed `ProgressStore` with an in-memory index in front of it.
- Position saved during playback; silent auto-resume on the next play.
- A watched flag at ≥90%, which drops the saved position.
- Progress bars on episode rows (Detail) and poster cards (Home / Discover /
  Search).
- Forget: context menu on rows and cards, a Detail-header button, a Settings
  list of in-progress titles, and Reset all progress.

Out of scope (deferred):

- A Continue Watching row on Home. Needs its own row source, ordering, and
  eviction rules — a separate roadmap item.
- Trakt or any other cross-device sync. This milestone is local-only.
- Per-title watch history (a list of every session). Only the latest position
  per `(media_id, video_id)` is kept.

## Architecture

Clean Architecture is preserved (`presentation → application → domain ←
infrastructure`). New pieces per layer:

- **domain**: a `PlaybackProgress` model, a `ProgressStore` port, and a
  `start` keyword on the `MediaPlayer.play` port method.
- **application**: `WatchProgressRepository` — the in-memory index and every
  policy rule. It lives in `application` for the same reason `AddonRepository`
  does: `application` must not import `infrastructure`.
- **infrastructure**: `SqliteProgressStore`, and the `start` option in
  `MpvPlayer.play`.
- **presentation**: `ProgressController`, `WatchedListModel`, new roles on
  `EpisodeListModel` and `PosterGridModel`, media context on
  `PlayerController`, two new QML components (`ContextMenu`, `ConfirmDialog`),
  and the bars/menus/Settings surfaces.
- **composition root**: wire it all in `main.py`.

## Components

### 1. Domain changes

New model in `domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class PlaybackProgress:
    media_id: str
    video_id: str  # "" for movies
    type: MediaType
    name: str
    poster: str | None
    label: str  # "S1E3 · Pilot", "" for movies
    position: float
    duration: float
    watched: bool
    updated_at: int  # unix seconds

    @property
    def fraction(self) -> float:
        """0.0–1.0 for the bar. 1.0 when watched (position is zeroed then)."""
```

`name`, `poster`, and `label` are denormalized onto the row so the Settings list
renders without a network fetch — there is no other local source for a title's
name once the addon catalog has moved on. `watched` is explicit rather than
derived from `fraction >= 0.9`, because a watched row drops its position to 0;
without the flag, watched and never-started would be indistinguishable.

New port in `domain/ports.py`:

```python
class ProgressStore(Protocol):
    """Durable store for playback progress. load_all() must never raise on
    missing or corrupt data — it returns an empty list instead."""

    def load_all(self) -> list[PlaybackProgress]: ...
    def save(self, entry: PlaybackProgress) -> None: ...
    def delete(self, media_id: str, video_id: str | None = None) -> None: ...
    def clear(self) -> None: ...
```

`delete` with `video_id=None` removes every row for that media (a whole series);
with a string, it removes exactly that row.

Changed port — `MediaPlayer.play` gains a keyword:

```python
def play(self, url: str, *, start: float = 0.0) -> None: ...
```

This is how resume works. mpv's `start` option is applied at load time, so there
is no seek-after-file-loaded race and no need to observe a load event.

### 2. SQLite store (infrastructure/progress/sqlite_store.py)

```sql
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
) WITHOUT ROWID;
```

- Path: `default_progress_path()` → `XDG_DATA_HOME/gravitas/progress.db`,
  falling back to `~/.local/share`. Progress is data, not config —
  `settings.json` stays in `XDG_CONFIG_HOME` where it is. Mirrors
  `default_settings_path()` in shape.
- One `sqlite3.Connection`, opened lazily, `check_same_thread=False`,
  `journal_mode=WAL`, `synchronous=NORMAL`. WAL means a write does not block the
  read side and an unclean exit cannot corrupt the file.
- `save` is a single `INSERT … ON CONFLICT(media_id, video_id) DO UPDATE`, on
  the GUI thread. It touches one row of a `WITHOUT ROWID` B-tree — microseconds,
  independent of how many rows exist. No debounce machinery needed beyond the
  5s tick.
- `user_version` pragma holds the schema version, so a future column is a
  migration and not a wipe. v1 sets it to 1.
- Every method is failure-tolerant, matching `JsonSettingsStore`: a broken or
  unwritable DB logs a warning and the session keeps working with progress
  disabled. `load_all` returns `[]`, never raises.

### 3. WatchProgressRepository (application/watch_progress.py)

Owns the in-memory index and every policy rule. The store is injected.

State, built once from `load_all()` at construction:

- `_by_key: dict[tuple[str, str], PlaybackProgress]`
- `_latest: dict[str, PlaybackProgress]` — highest `updated_at` per `media_id`,
  watched or not. Backs `hasProgress()`.
- `_latest_unwatched: dict[str, PlaybackProgress]` — highest `updated_at` per
  `media_id` among unwatched rows only. What a series poster/row bar reads.

Reads (pure dict hits, no I/O — all three are read per grid cell on every
flick, so none may scan `_by_key`; each is maintained incrementally on
write instead):

- `get(media_id, video_id="") -> PlaybackProgress | None`
- `latest_for(media_id) -> PlaybackProgress | None`
- `latest_unwatched_for(media_id) -> PlaybackProgress | None` — like
  `latest_for`, but skips watched rows. `_latest` discards watched-state
  across ties, so it can't answer this; `_latest_unwatched` is kept in sync
  separately for exactly this read, including evicting its own pointer (and
  falling back to the next most recent unwatched episode of the same media,
  if any) the moment a write flips that pointer's row to watched. What a
  series poster/row bar reads, so a just-finished episode doesn't read as the
  whole show being done.
- `fraction_for(media_id, video_id="") -> float` — 0.0 when absent.
- `in_progress() -> list[PlaybackProgress]` — one entry per media (the latest),
  unwatched only, sorted by `updated_at` descending. Feeds the Settings list.
- `total_count() -> int` — every saved row, watched included; what `reset_all`
  actually deletes. Gates Settings' Reset-all affordance instead of
  `in_progress()`, which would hide it once every saved title is finished.

Writes:

- `record(media_id, video_id, type, name, poster, label, position, duration)`:
  - `position < 30.0` → ignore. A mis-click never leaves a stub.
  - `duration > 0 and position / duration >= 0.9` → store `watched=True`,
    `position=0.0`. The bar becomes a checkmark and a replay starts clean.
  - Otherwise store the position with `watched=False`.
  - Update all three dicts, then `store.save(entry)`.
- `mark_watched(media_id, video_id, …)` — the ≥90% outcome, forced from the
  context menu.
- `forget(media_id, video_id=None)` — drop from all three dicts (rebuilding
  `_latest` and `_latest_unwatched` for that media when a single episode is
  removed), then `store.delete(...)`.
- `reset_all()` — clear all three dicts, then `store.clear()`.

`resume_position(media_id, video_id="") -> float` returns the entry's position
when it exists and is unwatched, else 0.0. `PlayerController` passes this
straight to `play(url, start=…)`.

### 4. PlayerController changes

The controller currently receives only a URL — `detail.playUrl(url)` carries no
media identity — so identity must be threaded through.

- New slot `setMediaContext(mediaId, videoId, type, name, poster, label)`,
  called by Detail/Sources immediately before `play(url)`. A separate slot
  rather than six more arguments on `play`, so the QML call sites that just
  want a URL stay unchanged.
- `play(url)` reads `repo.resume_position(...)` for the current context and
  calls `player.play(url, start=position)`. When the position is non-zero it
  emits `resumed(float)` → Player.qml shows a "Resumed from 30:20" toast.
- A `QTimer` (5000 ms) started on play, stopped on stop. On each tick, if not
  paused and the position is sane, call `repo.record(...)`. Also record on
  `pause`, on `stop`, and on app quit (`aboutToQuit`).
- Emits `progressRecorded()` so the models can refresh their bars when the user
  returns to Detail.

Guard: `record` is skipped when no media context is set, so a stream played from
an unknown surface never writes a nameless row.

### 5. Presentation — models

Bars come from **model roles**, not per-delegate controller calls. A role is
read once per delegate by the QML engine and re-read only on `dataChanged`,
which is exactly the invalidation we want; a slot would re-query on every binding
re-evaluation.

- `EpisodeListModel` gains `ProgressFractionRole` → `progressFraction` (float)
  and `WatchedRole` → `watched` (bool). Both read the repo by
  `(media_id, video.id)`; the model takes the repo and the current `media_id`.
- `PosterGridModel` gains the same two roles. A movie reads its own entry; a
  series reads `latest_unwatched_for(media_id)` (not `latest_for`) — a
  finished episode must not read as the whole show being 100% done, which
  would also disagree with `in_progress()` (watched-exclusive) simultaneously
  saying the same show is not in progress. Both are cheap, so a cell costs
  nothing. `PosterGridProxy` passes roles through untouched.
- `watched` is the `(media_id, "")` row for both kinds: for a movie that is the
  movie; for a series it is the marker written when the user marks the show
  finished from its context menu. A grid cannot infer that for a series — it
  has no episode list — but it does not need to infer what the user stated. A
  finished *episode* still never badges the show, since an episode's row is
  keyed by its own `video_id`. A series that is marked finished and then
  resumed yields the badge back to the bar, so a poster never claims done while
  showing progress.

Role names were checked against every delegate that consumes these models per
the role-shadowing gotcha (a role named `detail` breaks `detail.someFunction()`
inside a delegate). Neither `progressFraction` nor `watched` collides with any
existing QML id.

`WatchedListModel` (QAbstractListModel) feeds the Settings list from
`repo.in_progress()`. Roles: `mediaId`, `videoId`, `type`, `name`, `poster`,
`label`, `progressFraction`. `set_entries(list[PlaybackProgress])`.

### 6. Presentation — ProgressController

QObject bridge for mutation and for the Settings list.

- Signals: `progressChanged()`, `errorOccurred(str)`.
- `@Slot(str, str) forget(mediaId, videoId)` — one episode or one movie.
- `@Slot(str) forgetMedia(mediaId)` — a whole title. Backs the Detail button.
- `@Slot(str, str, str, str, str, str, float) markWatched(...)` — context menu.
- `@Slot() resetAll()` — Settings, behind a confirm step.
- `@Slot() refreshWatched()` — repopulate `WatchedListModel` when Settings opens.
- Every mutation emits `progressChanged`; `EpisodeListModel` and
  `PosterGridModel` connect to it and emit `dataChanged` across their rows for
  the two progress roles only.

### 7. Presentation — QML

- **`EpisodeRow.qml`**: a 3px bar pinned to the row's bottom edge, width
  `progressFraction * row.width`, in `Theme` accent; hidden when the fraction is
  0. When `watched`, the bar is replaced by a check glyph in the row's trailing
  slot and the row's text dims.
- **`PosterCard.qml`**: the same bar across the poster's bottom edge, inside the
  existing rounded clip. Watched → check badge in the top-right corner.
- **`ContextMenu.qml`** — a new component. The project has no menu primitive and
  deliberately builds its own controls rather than using stock
  QtQuick.Controls, so this follows `TrackMenu.qml`: a themed `Popup` with a
  `model` of `{label, action}` entries, opened at the cursor by a right-click
  `TapHandler` / long-press. Reused by both surfaces below.
- **`ConfirmDialog.qml`** — a new component. No confirm primitive exists either.
  A modal `Popup` with a title, a body line, and cancel/confirm buttons
  (`AppButton`), the confirm one styled destructive. Reused by the Detail button
  and Reset all.
- **Context menu on `EpisodeRow.qml` and `PosterCard.qml`**: "Mark as watched"
  and "Forget progress", each calling `progressController`. Entries are hidden
  when they would be no-ops — no "Forget progress" with nothing saved, no "Mark
  as watched" on an already-watched row.
- **`Detail.qml`**: a "Forget progress" action in the header, visible only when
  the title has something saved, calling `forgetMedia` through a
  `ConfirmDialog` that names the title ("Forget progress for Breaking Bad?").
  It clears every episode at once, so it confirms.
- **`Settings.qml`**: a new `SettingsCard` titled "Watch progress" holding a
  list bound to `watchedListModel` — poster thumb, name, `label · 42%`, and a
  remove button per row. A row's remove button fires immediately: it is one
  title and re-watching restores it. Below the list, a **Reset all progress**
  button in the destructive style, behind a `ConfirmDialog` naming the count
  ("Forget progress for 23 titles? This cannot be undone."). The card shows an
  empty state when nothing is saved.

### 8. Composition root (main.py)

- Build `SqliteProgressStore()` → `WatchProgressRepository(store)`. Construct
  before the models, since they take the repo.
- Pass the repo to `EpisodeListModel`, `PosterGridModel`, `PlayerController`,
  and `ProgressController`.
- New context properties: `progressController`, `watchedListModel`. Add both to
  the `engine._gravitas_refs` keep-alive tuple, or they get GC'd to null.
- Connect `app.aboutToQuit` → `PlayerController.flush_progress()` so the final
  seconds of a session are not lost.

## Data flow

- **Play** → Detail sets the media context → `PlayerController.play(url)` →
  `repo.resume_position` → `player.play(url, start=1820.5)` → mpv opens the file
  already seeked → `resumed` toast.
- **During playback** → 5s `QTimer` tick → `repo.record(...)` → policy applied
  (30s floor, 90% flip) → dict updated → one SQLite UPSERT.
- **Back to Detail** → `progressRecorded` → `dataChanged` → episode row bar
  redraws from the dict. No I/O.
- **Grid scroll** → each cell reads `progressFraction` → dict hit → bar.
- **Forget** → `ProgressController` → `repo.forget` → dicts pruned →
  `store.delete` → `progressChanged` → bars vanish.
- **Startup** → `load_all()` → one query, all rows → two dicts built. Every read
  for the rest of the session is memory.

## Error handling

- The store swallows and logs every `sqlite3.Error`: a corrupt, missing, or
  read-only DB degrades to progress-disabled, never to a crash. `load_all`
  returns `[]`.
- The repository is pure and cannot fail; it holds no I/O.
- `PlayerController.record` is skipped without a media context, and when
  `duration <= 0` (mpv has not parsed the file yet), so a stub row with a
  nonsense fraction is impossible.
- `resetAll` is confirmed in QML before it reaches the controller.

## Testing

Python (unit-tested):

- `SqliteProgressStore` against `tmp_path`: round-trip, UPSERT overwrites rather
  than duplicates, `delete` by media vs by video, `clear`, `load_all` on a
  missing file → `[]`, `load_all` on a garbage file → `[]`, `user_version` set.
- `WatchProgressRepository` with a fake store: the 30s floor, the 90% flip
  (watched set, position zeroed), `latest_for` picking the highest
  `updated_at`, `_latest` rebuilt correctly when one episode of several is
  forgotten, `in_progress` ordering and watched-exclusion, `resume_position`
  returning 0.0 for a watched entry, `reset_all`.
- `PlaybackProgress.fraction`: 0 duration → 0.0, watched → 1.0, mid → ratio.
- `EpisodeListModel` / `PosterGridModel`: the two new roles resolve, series
  cells read `latest_for`, `dataChanged` fires on `progressChanged`.
- `WatchedListModel`: roles and rows.
- `PlayerController` with the existing `FakeMpv` seam (so still no libmpv):
  `play` passes `start=` from a saved position, no context → no record, the
  timer tick records, `pause`/`stop` record, `duration <= 0` → no record.
- `ProgressController`: each forget path delegates correctly and emits
  `progressChanged`.
- `MpvPlayer.play(url, start=…)` sets mpv's `start` option (FakeMpv assertion).

QML is verified at launch, as always.

Quality gates (`ruff check`, `ruff format --check`, `mypy src`) pass; full suite
green.

## Deferred / follow-ups

- Continue Watching row on Home, sourced from `repo.in_progress()`.
- Eviction of very old entries (the table grows without bound today; a few
  thousand rows is nothing, but a cap or a TTL belongs here eventually).
- Trakt sync — the store seam is the natural place to add a second backend.
- "Next episode" autoplay, which would read the same index.
