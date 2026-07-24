# External Ratings (Rotten Tomatoes + Letterboxd) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Rotten Tomatoes and Letterboxd scores next to the existing IMDb rating on the Detail page, sourced from MDBList via a user-supplied API key.

**Architecture:** Mirror the existing `ExternalIdResolver`/`TmdbResolver`/`tmdb_key` path. A new `RatingsResolver` domain port is implemented by an `MdbListResolver` infrastructure adapter; a `GetRatings` use case wraps it with fault isolation; `DetailController` fetches ratings non-blocking after meta loads and exposes them as QML properties; `Detail.qml` renders two extra pills. A new `mdblist_key` setting is entered in Settings and persisted like `tmdb_key`.

**Tech Stack:** Python 3, PySide6 (Qt6/QML), httpx (async), qasync, pytest + respx.

## Global Constraints

- Clean Architecture dependency rule holds: `presentation → application → domain ← infrastructure`. `application` must NOT import `infrastructure`. Only `main.py` wires concrete adapters.
- All three quality gates must pass on every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- `mypy --strict` covers `src` only. `python-mpv`/`qasync` are untyped — use precise `# type: ignore[code]`, never blanket ignores.
- Domain layer imports nothing from other layers, Qt, or httpx. Every error subclasses `GravitasError`.
- Only `movie` and `series` media types exist.
- Tests never require libmpv or a display; Qt tests run headless (`QT_QPA_PLATFORM=offscreen`, set in `tests/conftest.py`).
- Run everything through `uv` (e.g. `uv run pytest -q`).
- Never add Claude attribution to commit messages.

---

### Task 1: Domain — `Ratings` model, `RatingsResolver` port, `MdbListUnavailable` error

**Files:**
- Modify: `src/gravitas/domain/models.py` (add `Ratings` near other frozen models)
- Modify: `src/gravitas/domain/errors.py` (add `MdbListUnavailable`)
- Modify: `src/gravitas/domain/ports.py` (add `RatingsResolver`, import `Ratings`)
- Test: `tests/domain/test_models.py` (add a `Ratings` test)

**Interfaces:**
- Produces: `Ratings(rotten_tomatoes: str | None = None, rotten_tomatoes_fresh: bool | None = None, letterboxd: str | None = None)` — frozen, slotted dataclass. `class MdbListUnavailable(GravitasError)`. `RatingsResolver` Protocol with `async def ratings(self, imdb_id: str) -> Ratings`.

- [ ] **Step 1: Write the failing test**

Add to `tests/domain/test_models.py`:

```python
def test_ratings_defaults_to_all_none():
    from gravitas.domain.models import Ratings

    r = Ratings()
    assert r.rotten_tomatoes is None
    assert r.rotten_tomatoes_fresh is None
    assert r.letterboxd is None


def test_ratings_is_frozen():
    import dataclasses
    import pytest

    from gravitas.domain.models import Ratings

    r = Ratings(rotten_tomatoes="87", rotten_tomatoes_fresh=True, letterboxd="4.1")
    assert (r.rotten_tomatoes, r.rotten_tomatoes_fresh, r.letterboxd) == ("87", True, "4.1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.letterboxd = "3.0"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py::test_ratings_defaults_to_all_none -v`
Expected: FAIL with `ImportError: cannot import name 'Ratings'`.

- [ ] **Step 3: Add the model, error, and port**

In `src/gravitas/domain/models.py`, add (near `MetaDetail`):

```python
@dataclass(frozen=True, slots=True)
class Ratings:
    """External critic/audience scores for a title, keyed by its IMDb id.

    All optional: a missing source is None and renders nothing. IMDb is NOT
    here — it comes from the addon's own meta. `rotten_tomatoes` is a percent
    string with no sign (e.g. "87"); `letterboxd` is out of 5 (e.g. "4.1")."""

    rotten_tomatoes: str | None = None
    rotten_tomatoes_fresh: bool | None = None
    letterboxd: str | None = None
```

In `src/gravitas/domain/errors.py`, append:

```python
class MdbListUnavailable(GravitasError):
    """MDBList could not be reached or returned an unusable response."""
```

In `src/gravitas/domain/ports.py`, add `Ratings` to the existing `from gravitas.domain.models import (...)` import block, then add (near `ExternalIdResolver`):

```python
@runtime_checkable
class RatingsResolver(Protocol):
    async def ratings(self, imdb_id: str) -> Ratings: ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
git add src/gravitas/domain/models.py src/gravitas/domain/errors.py src/gravitas/domain/ports.py tests/domain/test_models.py
git commit -m "feat(domain): Ratings model, RatingsResolver port, MdbListUnavailable"
```

---

### Task 2: Infrastructure — `MdbListResolver` + pure `_parse_ratings`

**Files:**
- Create: `src/gravitas/infrastructure/metadata/mdblist_resolver.py`
- Test: `tests/infrastructure/metadata/test_mdblist_resolver.py`

**Interfaces:**
- Consumes: `Ratings` (Task 1), `MdbListUnavailable` (Task 1).
- Produces: `MdbListResolver(client: httpx.AsyncClient, get_key: Callable[[], str | None])` with `async def ratings(self, imdb_id: str) -> Ratings`; module-level pure `_parse_ratings(payload: dict[str, Any]) -> Ratings`.

> **Verify against a live key before finalizing:** this uses the media-type-agnostic endpoint `https://api.mdblist.com/?apikey=KEY&i={imdb_id}` and expects `payload["ratings"]` to be a list of `{"source": str, "value": number}`. Confirm the exact `source` strings (`"tomatoes"`, `"letterboxd"`) and the **Letterboxd scale**. If MDBList returns Letterboxd on a 0–10 scale, halve it in `_score_lb` to show out of 5. The parser is defensive: unknown/missing/malformed sources yield `None` (no pill), so a wrong guess degrades gracefully rather than crashing.

- [ ] **Step 1: Write the failing tests**

Create `tests/infrastructure/metadata/test_mdblist_resolver.py`:

```python
import httpx
import pytest
import respx

from gravitas.domain.errors import MdbListUnavailable
from gravitas.infrastructure.metadata.mdblist_resolver import (
    MdbListResolver,
    _parse_ratings,
)

_PAYLOAD = {
    "ratings": [
        {"source": "imdb", "value": 8.0},
        {"source": "tomatoes", "value": 87},
        {"source": "audience", "value": 91},
        {"source": "letterboxd", "value": 4.1},
        {"source": "metacritic", "value": 74},
    ]
}


def test_parse_extracts_rt_and_letterboxd():
    r = _parse_ratings(_PAYLOAD)
    assert r.rotten_tomatoes == "87"
    assert r.rotten_tomatoes_fresh is True
    assert r.letterboxd == "4.1"


def test_parse_rotten_when_below_60():
    r = _parse_ratings({"ratings": [{"source": "tomatoes", "value": 42}]})
    assert r.rotten_tomatoes == "42"
    assert r.rotten_tomatoes_fresh is False


def test_parse_missing_sources_are_none():
    r = _parse_ratings({"ratings": [{"source": "imdb", "value": 8.0}]})
    assert r.rotten_tomatoes is None
    assert r.rotten_tomatoes_fresh is None
    assert r.letterboxd is None


def test_parse_null_and_malformed_values_are_none():
    r = _parse_ratings(
        {"ratings": [{"source": "tomatoes", "value": None}, {"source": "letterboxd"}]}
    )
    assert r.rotten_tomatoes is None
    assert r.letterboxd is None


def test_parse_no_ratings_key():
    assert _parse_ratings({}) == _parse_ratings({"ratings": "nope"})


@pytest.mark.asyncio
@respx.mock
async def test_ratings_fetches_and_caches():
    route = respx.get("https://api.mdblist.com/").mock(
        return_value=httpx.Response(200, json=_PAYLOAD)
    )
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        first = await resolver.ratings("tt1375666")
        second = await resolver.ratings("tt1375666")
    assert first.rotten_tomatoes == "87"
    assert first.letterboxd == "4.1"
    assert second == first
    assert route.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_ratings_without_key_raises():
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: None)
        with pytest.raises(MdbListUnavailable):
            await resolver.ratings("tt1375666")


@pytest.mark.asyncio
@respx.mock
async def test_ratings_http_error_raises_mdblist_unavailable():
    respx.get("https://api.mdblist.com/").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        resolver = MdbListResolver(client, lambda: "KEY")
        with pytest.raises(MdbListUnavailable):
            await resolver.ratings("tt1375666")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/infrastructure/metadata/test_mdblist_resolver.py -q`
Expected: FAIL with `ModuleNotFoundError: ... mdblist_resolver`.

- [ ] **Step 3: Write the implementation**

Create `src/gravitas/infrastructure/metadata/mdblist_resolver.py`:

```python
"""RatingsResolver backed by MDBList — RT + Letterboxd scores by IMDb id."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from gravitas.domain.errors import MdbListUnavailable
from gravitas.domain.models import Ratings
from gravitas.infrastructure.cache.ttl_cache import TtlCache

_API = "https://api.mdblist.com/"
_TTL = 60 * 60 * 24  # ratings move slowly; 24h like META_TTL


def _number(value: Any) -> float | None:
    # bool is an int subclass — reject it so True/False never counts as a score.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _score_pct(value: Any) -> str | None:
    n = _number(value)
    return str(int(round(n))) if n is not None else None


def _score_lb(value: Any) -> str | None:
    # MDBList Letterboxd `value` is out of 5 (verify against a live response;
    # if it is 0-10, divide by 2 here).
    n = _number(value)
    return f"{n:.1f}" if n is not None else None


def _parse_ratings(payload: dict[str, Any]) -> Ratings:
    by_source: dict[str, Any] = {}
    raw = payload.get("ratings")
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict):
                src = entry.get("source")
                if isinstance(src, str):
                    by_source[src.lower()] = entry.get("value")

    rt = _score_pct(by_source.get("tomatoes"))
    fresh = (int(rt) >= 60) if rt is not None else None
    return Ratings(
        rotten_tomatoes=rt,
        rotten_tomatoes_fresh=fresh,
        letterboxd=_score_lb(by_source.get("letterboxd")),
    )


class MdbListResolver:
    def __init__(self, client: httpx.AsyncClient, get_key: Callable[[], str | None]) -> None:
        self._client = client
        self._get_key = get_key
        self._cache: TtlCache[Ratings] = TtlCache()

    async def ratings(self, imdb_id: str) -> Ratings:
        key = self._get_key()
        if not key:
            raise MdbListUnavailable("add an MDBList API key in Settings for RT/Letterboxd")

        cached = self._cache.get(imdb_id)
        if cached is not None:
            return cached

        try:
            resp = await self._client.get(_API, params={"apikey": key, "i": imdb_id}, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MdbListUnavailable(f"MDBList request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise MdbListUnavailable("unexpected MDBList response")

        ratings = _parse_ratings(data)
        self._cache.put(imdb_id, ratings, _TTL)
        return ratings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/metadata/test_mdblist_resolver.py -q`
Expected: PASS (all 8).

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
git add src/gravitas/infrastructure/metadata/mdblist_resolver.py tests/infrastructure/metadata/test_mdblist_resolver.py
git commit -m "feat(infra): MdbListResolver — RT + Letterboxd ratings by imdb id"
```

---

### Task 3: Application — `GetRatings` use case

**Files:**
- Create: `src/gravitas/application/get_ratings.py`
- Test: `tests/application/test_get_ratings.py`

**Interfaces:**
- Consumes: `RatingsResolver` port (Task 1), `Ratings` (Task 1), `GravitasError`/`MdbListUnavailable` (Task 1).
- Produces: `GetRatings(resolver: RatingsResolver)` with `async def __call__(self, imdb_id: str) -> Ratings`. Never raises; returns `Ratings()` on any `GravitasError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/application/test_get_ratings.py`:

```python
import pytest

from gravitas.application.get_ratings import GetRatings
from gravitas.domain.errors import MdbListUnavailable
from gravitas.domain.models import Ratings


class _FakeResolver:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls: list[str] = []

    async def ratings(self, imdb_id: str) -> Ratings:
        self.calls.append(imdb_id)
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_returns_resolved_ratings():
    resolver = _FakeResolver(result=Ratings(rotten_tomatoes="87", letterboxd="4.1"))
    get = GetRatings(resolver)
    r = await get("tt1375666")
    assert r.rotten_tomatoes == "87"
    assert resolver.calls == ["tt1375666"]


@pytest.mark.asyncio
async def test_swallows_gravitas_error_into_empty():
    get = GetRatings(_FakeResolver(error=MdbListUnavailable("down")))
    assert await get("tt1375666") == Ratings()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/application/test_get_ratings.py -q`
Expected: FAIL with `ModuleNotFoundError: ... get_ratings`.

- [ ] **Step 3: Write the implementation**

Create `src/gravitas/application/get_ratings.py`:

```python
"""Use case: fetch external ratings (RT + Letterboxd) for a title by imdb id.

Fault-isolated: any GravitasError becomes an empty Ratings so a ratings outage
never breaks the Detail page."""

from __future__ import annotations

from gravitas.domain.errors import GravitasError
from gravitas.domain.models import Ratings
from gravitas.domain.ports import RatingsResolver


class GetRatings:
    def __init__(self, resolver: RatingsResolver) -> None:
        self._resolver = resolver

    async def __call__(self, imdb_id: str) -> Ratings:
        try:
            return await self._resolver.ratings(imdb_id)
        except GravitasError:
            return Ratings()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/application/test_get_ratings.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
git add src/gravitas/application/get_ratings.py tests/application/test_get_ratings.py
git commit -m "feat(app): GetRatings use case with fault isolation"
```

---

### Task 4: Settings persistence — `mdblist_key` on `PersistedSettings` + `JsonSettingsStore`

**Files:**
- Modify: `src/gravitas/domain/models.py` (`PersistedSettings`)
- Modify: `src/gravitas/infrastructure/settings/json_store.py` (`load`/`save`)
- Create: `tests/infrastructure/settings/__init__.py`
- Create: `tests/infrastructure/settings/test_json_store.py`

**Interfaces:**
- Produces: `PersistedSettings.mdblist_key: str | None = None`; `JsonSettingsStore` round-trips it under JSON key `"mdblist_key"`.

- [ ] **Step 1: Write the failing test**

Create `tests/infrastructure/settings/__init__.py` (empty) and `tests/infrastructure/settings/test_json_store.py`:

```python
from pathlib import Path

from gravitas.domain.models import PersistedSettings
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/settings/test_json_store.py -q`
Expected: FAIL with `TypeError` (unexpected `mdblist_key`) or `AttributeError`.

- [ ] **Step 3: Add the field and round-trip it**

In `src/gravitas/domain/models.py`, add to `PersistedSettings` (after `tmdb_key`):

```python
    mdblist_key: str | None = None
```

In `src/gravitas/infrastructure/settings/json_store.py` `load()`, after the `tmdb_key` handling, add:

```python
        raw_mdb = data.get("mdblist_key")
        mdb_key = raw_mdb if isinstance(raw_mdb, str) and raw_mdb else None
```

and pass it in the returned `PersistedSettings(..., mdblist_key=mdb_key)`.

In `save()`, add to the `payload` dict:

```python
            "mdblist_key": settings.mdblist_key,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/infrastructure/settings/test_json_store.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
git add src/gravitas/domain/models.py src/gravitas/infrastructure/settings/json_store.py tests/infrastructure/settings/
git commit -m "feat(settings): persist mdblist_key"
```

---

### Task 5: `SettingsController` — MDBList key property + slot + persist

**Files:**
- Modify: `src/gravitas/presentation/controllers/settings_controller.py`
- Test: `tests/presentation/test_settings_controller.py` (add cases)

**Interfaces:**
- Consumes: `_KeyHolder` Protocol (existing, `.key: str | None`), `PersistedSettings.mdblist_key` (Task 4).
- Produces: `SettingsController(..., mdblist_key_holder: _KeyHolder | None = None)`; `mdblistKey` Property(str); `setMdblistKey(str)` Slot; `mdblistKeyChanged` Signal. `persist()` writes `mdblist_key`.

- [ ] **Step 1: Write the failing test**

Look at the existing `tests/presentation/test_settings_controller.py` for its fakes, then add:

```python
def test_set_mdblist_key_updates_and_persists():
    from gravitas.presentation.controllers.settings_controller import SettingsController

    # reuse this file's existing fakes for uninstall/repo/model/catalog/store
    tmdb_holder = _KeyHolder()  # existing helper in this test module
    mdb_holder = _KeyHolder()
    store = _RecordingStore()  # existing helper capturing saved PersistedSettings
    ctrl = SettingsController(
        _FakeUninstall(),
        _FakeRepo(),
        _FakeModel(),
        _FakeCatalog(),
        tmdb_holder,
        store,
        _FakeStyleHolder(),
        mdb_holder,
    )

    assert ctrl.mdblistKey == ""
    ctrl.setMdblistKey("  key-xyz  ")
    assert mdb_holder.key == "key-xyz"  # trimmed
    assert ctrl.mdblistKey == "key-xyz"
    assert store.saved[-1].mdblist_key == "key-xyz"

    ctrl.setMdblistKey("")
    assert mdb_holder.key is None  # empty clears
```

> If the existing test module lacks a matching `_KeyHolder`/`_RecordingStore`/fakes, adapt to whatever doubles it already defines (they exist for the `tmdbKey` tests). Keep the assertions above.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_settings_controller.py -q`
Expected: FAIL (`SettingsController` takes no `mdblist_key_holder`, no `mdblistKey`).

- [ ] **Step 3: Implement**

In `settings_controller.py`:

Add signal in the class body next to the others:

```python
    mdblistKeyChanged = Signal()
```

Add constructor param (after `style_holder`) and store it:

```python
mdblist_key_holder: _KeyHolder | None = (None,)
```
```python
        self._mdblist_key_holder = mdblist_key_holder
```

Add property + slot (mirroring `tmdbKey`):

```python
@Property(str, notify=mdblistKeyChanged)
def mdblistKey(self) -> str:
    if self._mdblist_key_holder is not None and self._mdblist_key_holder.key:
        return self._mdblist_key_holder.key
    return ""


@Slot(str)
def setMdblistKey(self, key: str) -> None:
    if self._mdblist_key_holder is not None:
        self._mdblist_key_holder.key = key.strip() or None
        self.mdblistKeyChanged.emit()
    self.persist()
```

In `persist()`, read the mdblist key and include it in the saved `PersistedSettings`:

```python
        mdb = self._mdblist_key_holder.key if self._mdblist_key_holder is not None else None
```
```python
        self._store.save(
            PersistedSettings(
                addon_urls=tuple(self._repo.user_addon_urls()),
                tmdb_key=key,
                mdblist_key=mdb,
                subtitle_style=style,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/presentation/test_settings_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
git add src/gravitas/presentation/controllers/settings_controller.py tests/presentation/test_settings_controller.py
git commit -m "feat(settings): MDBList key property + persist in SettingsController"
```

---

### Task 6: `DetailController` — non-blocking ratings fetch + QML properties

**Files:**
- Modify: `src/gravitas/presentation/controllers/detail_controller.py`
- Test: `tests/presentation/test_detail_controller.py` (add cases)

**Interfaces:**
- Consumes: `GetRatings` (Task 3), `Ratings` (Task 1).
- Produces: `DetailController(..., get_ratings: GetRatings | None = None)`; Properties `rottenTomatoes: str`, `rottenTomatoesFresh: bool`, `letterboxd: str`; Signal `ratingsChanged`. After meta loads, if `get_ratings` is set and `meta.id` starts with `tt`, schedules `_load_ratings(token, imdb_id)` (fire-and-forget, token-guarded).

- [ ] **Step 1: Write the failing test**

Add to `tests/presentation/test_detail_controller.py` (it already has `qapp`/async helpers and a `FakeGetDetail`; reuse them):

```python
class FakeGetRatings:
    def __init__(self, result):
        self._result = result
        self.calls: list[str] = []

    async def __call__(self, imdb_id: str):
        self.calls.append(imdb_id)
        return self._result


@pytest.mark.asyncio
async def test_loads_ratings_after_meta(qapp):
    from gravitas.domain.models import Ratings

    # FakeGetDetail here must return meta with id="tt123" (imdb id).
    ratings = Ratings(rotten_tomatoes="87", rotten_tomatoes_fresh=True, letterboxd="4.1")
    get_ratings = FakeGetRatings(ratings)
    ctrl = _make_controller(get_ratings=get_ratings)  # build via this module's helper/fixtures

    await ctrl.load("movie", "tt123")
    await _drain_pending_tasks()  # let the fire-and-forget ratings task run

    assert get_ratings.calls == ["tt123"]
    assert ctrl.rottenTomatoes == "87"
    assert ctrl.rottenTomatoesFresh is True
    assert ctrl.letterboxd == "4.1"


@pytest.mark.asyncio
async def test_skips_ratings_for_non_imdb_id(qapp):
    get_ratings = FakeGetRatings(None)
    ctrl = _make_controller(get_ratings=get_ratings)
    await ctrl.load("movie", "tmdb:99")  # not a tt id
    await _drain_pending_tasks()
    assert get_ratings.calls == []
    assert ctrl.rottenTomatoes == ""


@pytest.mark.asyncio
async def test_ratings_default_empty_without_resolver(qapp):
    ctrl = _make_controller(get_ratings=None)
    await ctrl.load("movie", "tt123")
    assert ctrl.rottenTomatoes == ""
    assert ctrl.rottenTomatoesFresh is False
    assert ctrl.letterboxd == ""
```

> Adapt `_make_controller` / `_drain_pending_tasks` to this module's existing construction pattern and async helpers. For draining the fire-and-forget task, `await asyncio.sleep(0)` twice (or gather `asyncio.all_tasks()` minus current) is sufficient since `FakeGetRatings` never awaits I/O. Ensure the `FakeGetDetail` used by `_make_controller` returns a `MetaDetail` whose `id` is `"tt123"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/presentation/test_detail_controller.py -q`
Expected: FAIL (`DetailController` has no `get_ratings` param / no `rottenTomatoes`).

- [ ] **Step 3: Implement**

In `detail_controller.py`:

Add imports at top:

```python
import asyncio
```
```python
from gravitas.application.get_ratings import GetRatings
from gravitas.domain.models import MediaType, MetaDetail, Ratings, Video
```
(extend the existing `models` import to include `Ratings`.)

Add signal beside the others:

```python
    ratingsChanged = Signal()
```

Add constructor parameter (after `progress`) and initialise state:

```python
get_ratings: GetRatings | None = (None,)
```
```python
        self._get_ratings = get_ratings
        self._ratings: Ratings = Ratings()
```
(place `self._ratings` initialisation next to `self._meta: MetaDetail | None = None`.)

Add properties (near `imdbRating`):

```python
@Property(str, notify=ratingsChanged)
def rottenTomatoes(self) -> str:
    return self._ratings.rotten_tomatoes or ""


@Property(bool, notify=ratingsChanged)
def rottenTomatoesFresh(self) -> bool:
    return bool(self._ratings.rotten_tomatoes_fresh)


@Property(str, notify=ratingsChanged)
def letterboxd(self) -> str:
    return self._ratings.letterboxd or ""
```

In `load()`, in the reset block (right after `self.metaChanged.emit()` near line 266) add:

```python
        self._ratings = Ratings()
        self.ratingsChanged.emit()
```

Then, right after the success path sets meta (`self._meta = meta` / `self.metaChanged.emit()` near line 285-286), add:

```python
            if self._get_ratings is not None and meta.id.startswith("tt"):
                # Fire-and-forget: ratings must not delay the stream fetch below,
                # and GetRatings already swallows failures into empty Ratings.
                asyncio.ensure_future(self._load_ratings(token, meta.id))
```

Add the helper method:

```python
    async def _load_ratings(self, token: int, imdb_id: str) -> None:
        get_ratings = self._get_ratings
        if get_ratings is None:
            return
        ratings = await get_ratings(imdb_id)
        if token != self._seq:
            return  # a newer load started; drop this stale result
        self._ratings = ratings
        self.ratingsChanged.emit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/presentation/test_detail_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Gates + commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src
git add src/gravitas/presentation/controllers/detail_controller.py tests/presentation/test_detail_controller.py
git commit -m "feat(detail): non-blocking RT + Letterboxd ratings fetch"
```

---

### Task 7: QML — Detail pills + Settings MDBList field

**Files:**
- Modify: `src/gravitas/presentation/qml/Detail.qml` (meta row, after the IMDb pill ~line 127)
- Modify: `src/gravitas/presentation/qml/Settings.qml` (Metadata card, after the TMDB Row ~line 193)

**Interfaces:**
- Consumes: `detailController.rottenTomatoes/rottenTomatoesFresh/letterboxd` (Task 6), `settingsController.mdblistKey/setMdblistKey` (Task 5). QML is verified at launch (no unit tests).

- [ ] **Step 1: Add the RT + Letterboxd pills in `Detail.qml`**

In `Detail.qml`, immediately after the IMDb pill `Row { ... }` block closes (the `}` on ~line 127, still inside the outer meta `Row`), insert:

```qml
                Row {
                    spacing: 8
                    visible: detailController && detailController.rottenTomatoes.length > 0
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: detailController ? detailController.rottenTomatoes + "%" : ""
                        color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: rtBadge.implicitWidth + 12
                        height: 22
                        radius: 4
                        color: detailController && detailController.rottenTomatoesFresh ? "#fa320a" : "#00a000"
                        Text {
                            id: rtBadge
                            anchors.centerIn: parent
                            text: "RT"
                            color: "#ffffff"
                            font.pixelSize: Theme.fontSmall
                            font.bold: true
                        }
                    }
                }
                Row {
                    spacing: 8
                    visible: detailController && detailController.letterboxd.length > 0
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: detailController ? detailController.letterboxd : ""
                        color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: lbBadge.implicitWidth + 12
                        height: 22
                        radius: 4
                        color: "#14181c"
                        Row {
                            id: lbBadge
                            anchors.centerIn: parent
                            spacing: 4
                            Rectangle { width: 8; height: 8; radius: 4; color: "#ff8000"; anchors.verticalCenter: parent.verticalCenter }
                            Rectangle { width: 8; height: 8; radius: 4; color: "#00e054"; anchors.verticalCenter: parent.verticalCenter }
                            Rectangle { width: 8; height: 8; radius: 4; color: "#40bcf4"; anchors.verticalCenter: parent.verticalCenter }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Letterboxd"
                                color: "#ffffff"
                                font.pixelSize: Theme.fontSmall
                                font.bold: true
                            }
                        }
                    }
                }
```

- [ ] **Step 2: Add the MDBList key field in `Settings.qml`**

In `Settings.qml`, inside the `SettingsCard { title: "Metadata" ... }`, after the existing TMDB `Row { ... }` (closes ~line 193) and before the card closes, insert a sibling Row with unique ids:

```qml
                Row {
                    width: parent.width
                    spacing: 8
                    AppTextField {
                        width: parent.width - mdbSavedTick.width - parent.spacing
                        placeholderText: "MDBList API key (optional — enables Rotten Tomatoes + Letterboxd)"
                        text: settingsController ? settingsController.mdblistKey : ""
                        onEditingFinished: {
                            settingsController.setMdblistKey(text)
                            mdbSavedFade.restart()
                        }
                    }
                    Text {
                        id: mdbSavedTick
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Saved ✓"
                        color: Theme.positive
                        font.pixelSize: Theme.fontSmall
                        opacity: 0
                        SequentialAnimation {
                            id: mdbSavedFade
                            NumberAnimation { target: mdbSavedTick; property: "opacity"; to: 1; duration: Theme.durFast }
                            PauseAnimation { duration: 1400 }
                            NumberAnimation { target: mdbSavedTick; property: "opacity"; to: 0; duration: Theme.durMed }
                        }
                    }
                }
```

- [ ] **Step 3: Lint QML**

Run: `uv run qmllint src/gravitas/presentation/qml/Detail.qml src/gravitas/presentation/qml/Settings.qml` (or the project's qmllint invocation).
Expected: no new findings.

- [ ] **Step 4: Commit**

```bash
git add src/gravitas/presentation/qml/Detail.qml src/gravitas/presentation/qml/Settings.qml
git commit -m "feat(qml): RT + Letterboxd pills on Detail, MDBList key in Settings"
```

---

### Task 8: Compose — wire MDBList through `main.py`, full-suite + launch verify

**Files:**
- Modify: `src/gravitas/main.py`

**Interfaces:**
- Consumes: `MdbListResolver` (Task 2), `GetRatings` (Task 3), `DetailController(get_ratings=...)` (Task 6), `SettingsController(mdblist_key_holder=...)` (Task 5), `PersistedSettings.mdblist_key` (Task 4).

- [ ] **Step 1: Add imports**

In `src/gravitas/main.py`, alongside the existing metadata/application imports:

```python
from gravitas.application.get_ratings import GetRatings
from gravitas.infrastructure.metadata.mdblist_resolver import MdbListResolver
```

- [ ] **Step 2: Generalise the key holder and build the MDBList chain**

Rename the existing `class _TmdbKeyHolder:` to `class _KeyHolder:` (it is just `key: str | None = None`). Update the existing `tmdb_key = _TmdbKeyHolder()` to `tmdb_key = _KeyHolder()`. Then add, right after the `tmdb_resolver = TmdbResolver(...)` line:

```python
    mdblist_key = _KeyHolder()
    mdblist_key.key = persisted.mdblist_key
    mdblist_resolver = MdbListResolver(http, lambda: mdblist_key.key)
    get_ratings = GetRatings(mdblist_resolver)
```

- [ ] **Step 3: Pass into the controllers**

Change the `DetailController(...)` construction to:

```python
    detail_controller = DetailController(
        GetDetail(repo),
        ResolveStream(repo),
        stream_model,
        episode_model,
        progress_repo,
        get_ratings=get_ratings,
    )
```

Change the `SettingsController(...)` construction to pass the new holder last:

```python
    settings_controller = SettingsController(
        UninstallAddon(repo),
        repo,
        addon_list_model,
        catalog_controller,
        tmdb_key,
        settings_store,
        sub_style,
        mdblist_key,
    )
```

- [ ] **Step 4: Run the whole suite + gates**

Run:
```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy src
```
Expected: all tests PASS; all three gates clean.

- [ ] **Step 5: Launch verification**

With an MDBList key present in settings, launch and open a movie detail (an IMDb-id title). Confirm IMDb shows immediately and RT + Letterboxd pills appear a moment later; with no key, only IMDb shows and nothing errors.

Run: `uv run gravitas`
Expected: Detail page shows the three pills (given a key); no key → IMDb only, no error toast. (Use the `/run` or `verify` skill if available.)

- [ ] **Step 6: Commit**

```bash
git add src/gravitas/main.py
git commit -m "feat(app): wire MDBList ratings into Detail + Settings"
```

---

## Self-Review

**Spec coverage:**
- MDBList source, by imdb id → Task 2. ✅
- User-entered key mirroring tmdb_key → Tasks 4, 5, 7, 8. ✅
- IMDb from meta unchanged; RT + Letterboxd added → Tasks 6, 7. ✅
- Non-blocking async fetch after meta; token-guarded → Task 6. ✅
- Fetch guard (`tt…` + key set) → Tasks 2, 6. ✅
- 24h cache → Task 2. ✅
- Fault isolation (GravitasError → empty; pills hidden) → Tasks 2, 3, 6, 7. ✅
- Display: branded pills, RT fresh/rotten colour, Letterboxd /5 dots, hide-when-empty → Task 7. ✅
- `Ratings` model + `RatingsResolver` port + `MdbListUnavailable` → Task 1. ✅
- Settings persistence round-trip → Task 4. ✅
- Testing at every layer → Tasks 1-6. ✅
- Scope: detail page only, movie+series, no Metacritic/Trakt → honored (no catalog/grid tasks). ✅

**Type consistency:** `Ratings` fields (`rotten_tomatoes`, `rotten_tomatoes_fresh`, `letterboxd`) are identical across Tasks 1/2/3/6. Controller properties (`rottenTomatoes`, `rottenTomatoesFresh`, `letterboxd`) match QML bindings in Task 7. `MdbListResolver(client, get_key)` signature matches `main.py` wiring in Task 8. `GetRatings(resolver)` / `__call__(imdb_id)` consistent across Tasks 3/6/8. `SettingsController` new param `mdblist_key_holder` matches `main.py` positional arg order in Task 8.

**Open verification (flagged in Task 2):** MDBList endpoint form, exact `source` strings, and Letterboxd scale must be confirmed against a live key; the defensive parser degrades to "no pill" if any guess is off.
