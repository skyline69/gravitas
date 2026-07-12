# Gravitas MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Linux desktop app where a user adds a Stremio addon, browses its Movies/Series catalogs as a poster grid, opens a detail page, picks a direct-URL stream source, and plays it in an embedded libmpv player with a subtitle-track selector.

**Architecture:** Clean Architecture with a strict dependency rule `presentation → application → domain ← infrastructure`. The `domain` layer is pure Python (dataclasses + `Protocol` ports, no framework imports). `application` holds use cases depending only on domain ports. `infrastructure` provides adapters (httpx addon client, libmpv player, disk cache). `presentation` is Qt/QML glue (QObject controllers + `QAbstractListModel`s + QML). `main.py` is the only composition root.

**Tech Stack:** Python 3.12+, PySide6 (Qt6/QML), python-mpv (libmpv), httpx (async), qasync (asyncio-on-Qt-loop), platformdirs. Tooling: uv, ruff, mypy (strict), pytest (+ pytest-asyncio, pytest-qt, respx).

## Global Constraints

- Python `>=3.12`.
- Package/env manager: `uv` only. All commands run via `uv run ...`.
- `ruff check` and `ruff format --check` MUST pass on every commit.
- `mypy --strict` MUST pass on every commit (config in `pyproject.toml`).
- Dependency rule is absolute: `domain/` imports nothing from `application/`, `infrastructure/`, `presentation/`, Qt, or httpx. `application/` imports only from `domain/`. Violations are bugs.
- NO torrent engine, ever. The player receives only direct HTTP/HLS URLs. Debrid resolution is a later milestone (a `DebridResolver` port stub exists but is unused in MVP).
- Src layout: all code under `src/gravitas/`; tests under `tests/`.
- Every domain error subclasses `GravitasError`.

---

## File Structure

```
gravitas/
  pyproject.toml
  src/gravitas/
    __init__.py
    domain/
      __init__.py
      errors.py          # GravitasError + typed errors
      models.py          # MediaItem, Video, MetaDetail, Stream, Catalog, AddonManifest
      ports.py           # AddonSource, DebridResolver, MediaPlayer, Cache (Protocols)
    application/
      __init__.py
      install_addon.py    # InstallAddon
      browse_catalog.py   # BrowseCatalog
      get_detail.py       # GetDetail
      resolve_stream.py   # ResolveStream
    infrastructure/
      __init__.py
      addons/
        __init__.py
        parsing.py        # pure JSON dict -> domain model functions
        client.py         # AddonClient (httpx) implements AddonSource
        repository.py      # AddonRepository (installed addons + aggregation)
      cache/
        __init__.py
        disk_cache.py     # DiskCache implements Cache
      player/
        __init__.py
        mpv_player.py      # MpvPlayer implements MediaPlayer
    presentation/
      __init__.py
      models/
        __init__.py
        poster_grid_model.py  # PosterGridModel(QAbstractListModel)
        stream_list_model.py  # StreamListModel(QAbstractListModel)
      controllers/
        __init__.py
        catalog_controller.py
        detail_controller.py
        player_controller.py
      qml/
        Main.qml
        Home.qml
        Detail.qml
        Player.qml
        components/PosterCard.qml
        components/StreamRow.qml
    main.py               # composition root
  tests/
    conftest.py
    fixtures/             # recorded addon JSON
    ...
```

---

### Task 1: Project scaffold + tooling gate

**Files:**
- Create: `pyproject.toml`
- Create: `src/gravitas/__init__.py`
- Create: `tests/test_smoke.py`
- Create: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `uv` project where `uv run pytest`, `uv run ruff check`, `uv run mypy src` all pass.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "gravitas"
version = "0.1.0"
description = "Memory-efficient, Linux-first Stremio-addon media center"
requires-python = ">=3.12"
dependencies = [
    "pyside6>=6.9",
    "python-mpv>=1.0.7",
    "httpx>=0.27",
    "qasync>=0.27",
    "platformdirs>=4.2",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "pytest-qt>=4.4",
    "respx>=0.21",
    "ruff>=0.6",
    "mypy>=1.11",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/gravitas"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
mypy_path = "src"
packages = ["gravitas"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.mypy_cache/
.pytest_cache/
.ruff_cache/
dist/
uv.lock
```

- [ ] **Step 3: Create package marker and smoke test**

`src/gravitas/__init__.py`:
```python
"""Gravitas: a Stremio-addon media center."""

__version__ = "0.1.0"
```

`tests/test_smoke.py`:
```python
from gravitas import __version__


def test_version_present() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 4: Sync env and run gates**

Run: `uv sync`
Then: `uv run pytest -q`
Expected: `1 passed`.
Then: `uv run ruff check .`
Expected: `All checks passed!`
Then: `uv run mypy src`
Expected: `Success: no issues found`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore src/gravitas/__init__.py tests/test_smoke.py
git commit -m "chore: scaffold uv project with ruff/mypy/pytest gates"
```

---

### Task 2: Domain errors

**Files:**
- Create: `src/gravitas/domain/__init__.py`
- Create: `src/gravitas/domain/errors.py`
- Create: `tests/domain/test_errors.py`

**Interfaces:**
- Produces: `GravitasError(Exception)`; subclasses `AddonUnreachable`, `InvalidManifest`, `InvalidResponse`, `NoStreams`, `PlaybackFailed`. All accept a `str` message.

- [ ] **Step 1: Write the failing test**

`tests/domain/__init__.py`: (empty file)

`tests/domain/test_errors.py`:
```python
import pytest

from gravitas.domain.errors import (
    AddonUnreachable,
    GravitasError,
    InvalidManifest,
    InvalidResponse,
    NoStreams,
    PlaybackFailed,
)


@pytest.mark.parametrize(
    "exc",
    [AddonUnreachable, InvalidManifest, InvalidResponse, NoStreams, PlaybackFailed],
)
def test_all_errors_subclass_base(exc: type[GravitasError]) -> None:
    instance = exc("boom")
    assert isinstance(instance, GravitasError)
    assert str(instance) == "boom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_errors.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'gravitas.domain'`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/domain/__init__.py`: (empty file)

`src/gravitas/domain/errors.py`:
```python
"""Typed domain errors. Every failure the domain raises subclasses GravitasError."""


class GravitasError(Exception):
    """Base class for all Gravitas domain errors."""


class AddonUnreachable(GravitasError):
    """An addon endpoint could not be reached (network/transport failure)."""


class InvalidManifest(GravitasError):
    """An addon manifest was missing required fields or malformed."""


class InvalidResponse(GravitasError):
    """An addon resource response was malformed."""


class NoStreams(GravitasError):
    """No playable streams were returned for an item."""


class PlaybackFailed(GravitasError):
    """The media player failed to start or continue playback."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_errors.py -q`
Expected: `2 passed` (parametrized 5 → collected as multiple; all pass).
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/domain/__init__.py src/gravitas/domain/errors.py tests/domain/
git commit -m "feat(domain): add typed error hierarchy"
```

---

### Task 3: Domain models

**Files:**
- Create: `src/gravitas/domain/models.py`
- Create: `tests/domain/test_models.py`

**Interfaces:**
- Produces (all frozen dataclasses):
  - `MediaType` = `Literal["movie", "series"]`
  - `MediaItem(id: str, type: MediaType, name: str, poster: str | None)`
  - `Video(id: str, title: str, season: int | None, episode: int | None)`
  - `MetaDetail(id: str, type: MediaType, name: str, description: str | None, poster: str | None, background: str | None, videos: tuple[Video, ...])`
  - `Stream(name: str, title: str, url: str | None, info_hash: str | None, file_idx: int | None)` with property `is_direct: bool` (`url is not None`)
  - `CatalogRef(type: MediaType, id: str, name: str)`
  - `AddonManifest(id: str, name: str, version: str, resources: tuple[str, ...], types: tuple[str, ...], catalogs: tuple[CatalogRef, ...], base_url: str)`

- [ ] **Step 1: Write the failing test**

`tests/domain/test_models.py`:
```python
import dataclasses

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MetaDetail,
    Stream,
    Video,
)


def test_media_item_is_frozen() -> None:
    item = MediaItem(id="tt1", type="movie", name="Film", poster=None)
    assert item.name == "Film"
    with_pytest_raises = dataclasses.FrozenInstanceError
    try:
        item.name = "Other"  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except with_pytest_raises:
        pass


def test_stream_is_direct_when_url_present() -> None:
    direct = Stream(name="1080p", title="src", url="http://x/v.mkv", info_hash=None, file_idx=None)
    torrent = Stream(name="1080p", title="src", url=None, info_hash="abc", file_idx=0)
    assert direct.is_direct is True
    assert torrent.is_direct is False


def test_meta_detail_holds_episode_videos() -> None:
    meta = MetaDetail(
        id="tt2",
        type="series",
        name="Show",
        description="d",
        poster=None,
        background=None,
        videos=(Video(id="tt2:1:1", title="Pilot", season=1, episode=1),),
    )
    assert meta.videos[0].episode == 1


def test_manifest_catalogs() -> None:
    manifest = AddonManifest(
        id="a",
        name="Cinemeta",
        version="1.0",
        resources=("catalog", "meta", "stream"),
        types=("movie", "series"),
        catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
        base_url="https://x/",
    )
    assert manifest.catalogs[0].name == "Top"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/domain/models.py`:
```python
"""Pure domain entities. Frozen dataclasses, no framework imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MediaType = Literal["movie", "series"]


@dataclass(frozen=True, slots=True)
class MediaItem:
    id: str
    type: MediaType
    name: str
    poster: str | None


@dataclass(frozen=True, slots=True)
class Video:
    id: str
    title: str
    season: int | None
    episode: int | None


@dataclass(frozen=True, slots=True)
class MetaDetail:
    id: str
    type: MediaType
    name: str
    description: str | None
    poster: str | None
    background: str | None
    videos: tuple[Video, ...]


@dataclass(frozen=True, slots=True)
class Stream:
    name: str
    title: str
    url: str | None
    info_hash: str | None
    file_idx: int | None

    @property
    def is_direct(self) -> bool:
        return self.url is not None


@dataclass(frozen=True, slots=True)
class CatalogRef:
    type: MediaType
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class AddonManifest:
    id: str
    name: str
    version: str
    resources: tuple[str, ...]
    types: tuple[str, ...]
    catalogs: tuple[CatalogRef, ...]
    base_url: str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_models.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): add core entity dataclasses"
```

---

### Task 4: Domain ports (Protocols)

**Files:**
- Create: `src/gravitas/domain/ports.py`
- Create: `tests/domain/test_ports.py`

**Interfaces:**
- Produces these `Protocol`s (all `@runtime_checkable`):
  - `AddonSource`: `async fetch_manifest(url: str) -> AddonManifest`; `async fetch_catalog(manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]`; `async fetch_meta(manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail`; `async fetch_streams(manifest: AddonManifest, type: MediaType, id: str) -> list[Stream]`
  - `Cache`: `async get_or_fetch(url: str) -> bytes`
  - `MediaPlayer`: `play(url: str) -> None`; `pause() -> None`; `resume() -> None`; `seek(seconds: float) -> None`; `set_subtitle_track(track_id: int | None) -> None`; `subtitle_tracks() -> list[tuple[int, str]]`; `shutdown() -> None`
  - `DebridResolver` (unused in MVP): `async resolve(stream: Stream) -> str`

- [ ] **Step 1: Write the failing test**

`tests/domain/test_ports.py`:
```python
from gravitas.domain.models import AddonManifest, MediaItem
from gravitas.domain.ports import AddonSource


class FakeAddonSource:
    async def fetch_manifest(self, url: str) -> AddonManifest:  # pragma: no cover - shape only
        raise NotImplementedError

    async def fetch_catalog(self, manifest: AddonManifest, ref: object) -> list[MediaItem]:
        raise NotImplementedError

    async def fetch_meta(self, manifest: AddonManifest, type: str, id: str) -> object:
        raise NotImplementedError

    async def fetch_streams(self, manifest: AddonManifest, type: str, id: str) -> list[object]:
        raise NotImplementedError


def test_fake_satisfies_addon_source_protocol() -> None:
    assert isinstance(FakeAddonSource(), AddonSource)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_ports.py -q`
Expected: FAIL with `ImportError` (no `ports` module).

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/domain/ports.py`:
```python
"""Port interfaces (Protocols) the application depends on and infrastructure implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)


@runtime_checkable
class AddonSource(Protocol):
    async def fetch_manifest(self, url: str) -> AddonManifest: ...
    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]: ...
    async def fetch_meta(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> MetaDetail: ...
    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]: ...


@runtime_checkable
class Cache(Protocol):
    async def get_or_fetch(self, url: str) -> bytes: ...


@runtime_checkable
class MediaPlayer(Protocol):
    def play(self, url: str) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def set_subtitle_track(self, track_id: int | None) -> None: ...
    def subtitle_tracks(self) -> list[tuple[int, str]]: ...
    def shutdown(self) -> None: ...


@runtime_checkable
class DebridResolver(Protocol):
    """Later milestone: resolve an infoHash stream to a direct URL. Unused in MVP."""

    async def resolve(self, stream: Stream) -> str: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_ports.py -q`
Expected: pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/domain/ports.py tests/domain/test_ports.py
git commit -m "feat(domain): add port protocols"
```

---

### Task 5: Stremio protocol parsing (pure functions)

**Files:**
- Create: `src/gravitas/infrastructure/__init__.py`
- Create: `src/gravitas/infrastructure/addons/__init__.py`
- Create: `src/gravitas/infrastructure/addons/parsing.py`
- Create: `tests/infrastructure/addons/test_parsing.py`

**Interfaces:**
- Consumes: domain models, `InvalidManifest`, `InvalidResponse`.
- Produces (pure, no I/O):
  - `parse_manifest(data: dict, base_url: str) -> AddonManifest`
  - `parse_catalog(data: dict) -> list[MediaItem]`
  - `parse_meta(data: dict) -> MetaDetail`
  - `parse_streams(data: dict) -> list[Stream]`
  - `catalog_path(ref: CatalogRef) -> str` → `"catalog/{type}/{id}.json"`
  - `meta_path(type, id) -> str`; `stream_path(type, id) -> str`

- [ ] **Step 1: Write the failing test**

`tests/infrastructure/__init__.py`, `tests/infrastructure/addons/__init__.py`: (empty files)

`tests/infrastructure/addons/test_parsing.py`:
```python
import pytest

from gravitas.domain.errors import InvalidManifest
from gravitas.domain.models import CatalogRef
from gravitas.infrastructure.addons.parsing import (
    catalog_path,
    meta_path,
    parse_catalog,
    parse_manifest,
    parse_meta,
    parse_streams,
    stream_path,
)


def test_parse_manifest_maps_fields() -> None:
    data = {
        "id": "com.linvo.cinemeta",
        "name": "Cinemeta",
        "version": "3.0.0",
        "resources": ["catalog", "meta", "stream"],
        "types": ["movie", "series"],
        "catalogs": [{"type": "movie", "id": "top", "name": "Popular"}],
    }
    m = parse_manifest(data, base_url="https://v3-cinemeta.strem.io/")
    assert m.id == "com.linvo.cinemeta"
    assert m.base_url == "https://v3-cinemeta.strem.io/"
    assert m.catalogs[0] == CatalogRef(type="movie", id="top", name="Popular")


def test_parse_manifest_rejects_missing_id() -> None:
    with pytest.raises(InvalidManifest):
        parse_manifest({"name": "x"}, base_url="https://x/")


def test_parse_catalog_skips_unknown_types() -> None:
    data = {
        "metas": [
            {"id": "tt1", "type": "movie", "name": "A", "poster": "http://p/1.jpg"},
            {"id": "c1", "type": "channel", "name": "skip"},
        ]
    }
    items = parse_catalog(data)
    assert len(items) == 1
    assert items[0].id == "tt1"
    assert items[0].poster == "http://p/1.jpg"


def test_parse_meta_reads_videos() -> None:
    data = {
        "meta": {
            "id": "tt2",
            "type": "series",
            "name": "Show",
            "description": "d",
            "videos": [
                {"id": "tt2:1:1", "title": "Pilot", "season": 1, "episode": 1},
            ],
        }
    }
    meta = parse_meta(data)
    assert meta.videos[0].episode == 1


def test_parse_streams_direct_and_torrent() -> None:
    data = {
        "streams": [
            {"name": "1080p", "title": "web", "url": "http://s/v.mkv"},
            {"name": "720p", "title": "torr", "infoHash": "abc", "fileIdx": 2},
        ]
    }
    streams = parse_streams(data)
    assert streams[0].is_direct is True
    assert streams[1].info_hash == "abc"
    assert streams[1].file_idx == 2


def test_paths() -> None:
    assert catalog_path(CatalogRef(type="movie", id="top", name="T")) == "catalog/movie/top.json"
    assert meta_path("series", "tt2") == "meta/series/tt2.json"
    assert stream_path("movie", "tt1") == "stream/movie/tt1.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/infrastructure/__init__.py`, `src/gravitas/infrastructure/addons/__init__.py`: (empty files)

`src/gravitas/infrastructure/addons/parsing.py`:
```python
"""Pure functions mapping Stremio addon JSON to domain models. No I/O."""

from __future__ import annotations

from typing import Any, get_args

from gravitas.domain.errors import InvalidManifest, InvalidResponse
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
    Video,
)

_VALID_TYPES: frozenset[str] = frozenset(get_args(MediaType))


def _require(data: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in data:
        raise InvalidManifest(f"missing '{key}' in {ctx}")
    return data[key]


def parse_manifest(data: dict[str, Any], base_url: str) -> AddonManifest:
    manifest_id = _require(data, "id", "manifest")
    name = _require(data, "name", "manifest")
    catalogs: list[CatalogRef] = []
    for raw in data.get("catalogs", []):
        c_type = raw.get("type")
        if c_type not in _VALID_TYPES:
            continue
        catalogs.append(
            CatalogRef(type=c_type, id=raw.get("id", ""), name=raw.get("name", raw.get("id", "")))
        )
    return AddonManifest(
        id=str(manifest_id),
        name=str(name),
        version=str(data.get("version", "0.0.0")),
        resources=tuple(str(r) for r in data.get("resources", [])),
        types=tuple(str(t) for t in data.get("types", [])),
        catalogs=tuple(catalogs),
        base_url=base_url if base_url.endswith("/") else base_url + "/",
    )


def parse_catalog(data: dict[str, Any]) -> list[MediaItem]:
    metas = data.get("metas")
    if not isinstance(metas, list):
        raise InvalidResponse("catalog response missing 'metas' list")
    items: list[MediaItem] = []
    for raw in metas:
        m_type = raw.get("type")
        if m_type not in _VALID_TYPES:
            continue
        items.append(
            MediaItem(
                id=str(raw.get("id", "")),
                type=m_type,
                name=str(raw.get("name", "")),
                poster=raw.get("poster"),
            )
        )
    return items


def _parse_video(raw: dict[str, Any]) -> Video:
    return Video(
        id=str(raw.get("id", "")),
        title=str(raw.get("title", raw.get("name", ""))),
        season=raw.get("season"),
        episode=raw.get("episode"),
    )


def parse_meta(data: dict[str, Any]) -> MetaDetail:
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise InvalidResponse("meta response missing 'meta' object")
    m_type = meta.get("type")
    if m_type not in _VALID_TYPES:
        raise InvalidResponse(f"unsupported meta type: {m_type!r}")
    videos = tuple(_parse_video(v) for v in meta.get("videos", []) if isinstance(v, dict))
    return MetaDetail(
        id=str(meta.get("id", "")),
        type=m_type,
        name=str(meta.get("name", "")),
        description=meta.get("description"),
        poster=meta.get("poster"),
        background=meta.get("background"),
        videos=videos,
    )


def parse_streams(data: dict[str, Any]) -> list[Stream]:
    raw_streams = data.get("streams")
    if not isinstance(raw_streams, list):
        raise InvalidResponse("stream response missing 'streams' list")
    streams: list[Stream] = []
    for raw in raw_streams:
        streams.append(
            Stream(
                name=str(raw.get("name", "")),
                title=str(raw.get("title", raw.get("name", ""))),
                url=raw.get("url"),
                info_hash=raw.get("infoHash"),
                file_idx=raw.get("fileIdx"),
            )
        )
    return streams


def catalog_path(ref: CatalogRef) -> str:
    return f"catalog/{ref.type}/{ref.id}.json"


def meta_path(type: MediaType, id: str) -> str:
    return f"meta/{type}/{id}.json"


def stream_path(type: MediaType, id: str) -> str:
    return f"stream/{type}/{id}.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/infrastructure/__init__.py src/gravitas/infrastructure/addons/__init__.py src/gravitas/infrastructure/addons/parsing.py tests/infrastructure/
git commit -m "feat(addons): add Stremio protocol parsing"
```

---

### Task 6: AddonClient (httpx adapter)

**Files:**
- Create: `src/gravitas/infrastructure/addons/client.py`
- Create: `tests/infrastructure/addons/test_client.py`

**Interfaces:**
- Consumes: `parsing.*`, `AddonManifest`, `CatalogRef`, `AddonUnreachable`.
- Produces: `AddonClient(client: httpx.AsyncClient)` implementing `AddonSource`. A manifest URL ending in `/manifest.json` yields `base_url` = the URL with that suffix stripped. Transport errors raise `AddonUnreachable`.

- [ ] **Step 1: Write the failing test**

`tests/infrastructure/addons/test_client.py`:
```python
import httpx
import pytest
import respx

from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import CatalogRef
from gravitas.infrastructure.addons.client import AddonClient


@respx.mock
async def test_fetch_manifest_strips_suffix() -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={"id": "c", "name": "Cinemeta", "version": "3.0", "types": ["movie"],
                  "resources": ["catalog"], "catalogs": []},
        )
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        manifest = await client.fetch_manifest("https://cin.strem.io/manifest.json")
    assert manifest.base_url == "https://cin.strem.io/"


@respx.mock
async def test_fetch_catalog_builds_path() -> None:
    respx.get("https://cin.strem.io/manifest.json").mock(
        return_value=httpx.Response(
            200,
            json={"id": "c", "name": "C", "version": "1", "types": ["movie"],
                  "resources": ["catalog"], "catalogs": [{"type": "movie", "id": "top", "name": "T"}]},
        )
    )
    respx.get("https://cin.strem.io/catalog/movie/top.json").mock(
        return_value=httpx.Response(200, json={"metas": [
            {"id": "tt1", "type": "movie", "name": "A", "poster": "p"}]})
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        manifest = await client.fetch_manifest("https://cin.strem.io/manifest.json")
        items = await client.fetch_catalog(manifest, CatalogRef(type="movie", id="top", name="T"))
    assert items[0].id == "tt1"


@respx.mock
async def test_transport_error_becomes_addon_unreachable() -> None:
    respx.get("https://down/manifest.json").mock(side_effect=httpx.ConnectError("nope"))
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        with pytest.raises(AddonUnreachable):
            await client.fetch_manifest("https://down/manifest.json")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/addons/test_client.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/infrastructure/addons/client.py`:
```python
"""AddonSource implementation backed by an httpx.AsyncClient."""

from __future__ import annotations

from typing import Any

import httpx

from gravitas.domain.errors import AddonUnreachable, InvalidResponse
from gravitas.domain.models import AddonManifest, CatalogRef, MediaType, MetaDetail, Stream
from gravitas.infrastructure.addons import parsing

_MANIFEST_SUFFIX = "manifest.json"


class AddonClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = await self._client.get(url, follow_redirects=True, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AddonUnreachable(f"GET {url} failed: {exc}") from exc
        data = response.json()
        if not isinstance(data, dict):
            raise InvalidResponse(f"expected JSON object from {url}")
        return data

    async def fetch_manifest(self, url: str) -> AddonManifest:
        data = await self._get_json(url)
        base_url = url[: -len(_MANIFEST_SUFFIX)] if url.endswith(_MANIFEST_SUFFIX) else url
        return parsing.parse_manifest(data, base_url=base_url)

    async def fetch_catalog(
        self, manifest: AddonManifest, ref: CatalogRef
    ) -> list[MediaItem]:
        data = await self._get_json(manifest.base_url + parsing.catalog_path(ref))
        return parsing.parse_catalog(data)

    async def fetch_meta(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> MetaDetail:
        data = await self._get_json(manifest.base_url + parsing.meta_path(type, id))
        return parsing.parse_meta(data)

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        data = await self._get_json(manifest.base_url + parsing.stream_path(type, id))
        return parsing.parse_streams(data)
```

Also add the missing import at the top of the file — include `MediaItem` in the models import line:
```python
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)
```
(Replace the single-line models import above with this grouped import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/addons/test_client.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/infrastructure/addons/client.py tests/infrastructure/addons/test_client.py
git commit -m "feat(addons): add httpx AddonClient"
```

---

### Task 7: AddonRepository (installed addons + aggregation)

**Files:**
- Create: `src/gravitas/infrastructure/addons/repository.py`
- Create: `tests/infrastructure/addons/test_repository.py`

**Interfaces:**
- Consumes: `AddonSource`, `AddonManifest`, `CatalogRef`, `MediaItem`.
- Produces: `AddonRepository(source: AddonSource)` with:
  - `async install(url: str) -> AddonManifest` (fetches manifest, stores it, returns it)
  - `installed() -> list[AddonManifest]`
  - `catalog_refs() -> list[tuple[AddonManifest, CatalogRef]]` (flattened across addons)
  - `async aggregate_catalog(ref_owner: AddonManifest, ref: CatalogRef) -> list[MediaItem]` (delegates to source; a failing addon returns `[]` and is logged, never raises)

- [ ] **Step 1: Write the failing test**

`tests/infrastructure/addons/test_repository.py`:
```python
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import AddonManifest, CatalogRef, MediaItem, MediaType, MetaDetail, Stream
from gravitas.infrastructure.addons.repository import AddonRepository


class FakeSource:
    def __init__(self) -> None:
        self.fail_catalog = False

    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id=url, name="Fake", version="1", resources=("catalog",), types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top"),), base_url=url,
        )

    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        if self.fail_catalog:
            raise AddonUnreachable("down")
        return [MediaItem(id="tt1", type="movie", name="A", poster=None)]

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        raise NotImplementedError

    async def fetch_streams(self, manifest: AddonManifest, type: MediaType, id: str) -> list[Stream]:
        raise NotImplementedError


async def test_install_stores_manifest() -> None:
    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    assert len(repo.installed()) == 1
    assert repo.catalog_refs()[0][1].id == "top"


async def test_aggregate_catalog_returns_items() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")
    ref = manifest.catalogs[0]
    items = await repo.aggregate_catalog(manifest, ref)
    assert items[0].id == "tt1"


async def test_failing_addon_yields_empty_not_raise() -> None:
    source = FakeSource()
    repo = AddonRepository(source)
    manifest = await repo.install("https://a/")
    source.fail_catalog = True
    items = await repo.aggregate_catalog(manifest, manifest.catalogs[0])
    assert items == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/addons/test_repository.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/infrastructure/addons/repository.py`:
```python
"""In-memory store of installed addons plus catalog aggregation helpers."""

from __future__ import annotations

import logging

from gravitas.domain.errors import GravitasError
from gravitas.domain.models import AddonManifest, CatalogRef, MediaItem
from gravitas.domain.ports import AddonSource

_log = logging.getLogger(__name__)


class AddonRepository:
    def __init__(self, source: AddonSource) -> None:
        self._source = source
        self._manifests: list[AddonManifest] = []

    async def install(self, url: str) -> AddonManifest:
        manifest = await self._source.fetch_manifest(url)
        self._manifests = [m for m in self._manifests if m.id != manifest.id]
        self._manifests.append(manifest)
        return manifest

    def installed(self) -> list[AddonManifest]:
        return list(self._manifests)

    def catalog_refs(self) -> list[tuple[AddonManifest, CatalogRef]]:
        return [(m, ref) for m in self._manifests for ref in m.catalogs]

    async def aggregate_catalog(
        self, ref_owner: AddonManifest, ref: CatalogRef
    ) -> list[MediaItem]:
        try:
            return await self._source.fetch_catalog(ref_owner, ref)
        except GravitasError as exc:
            _log.warning("catalog fetch failed for %s/%s: %s", ref_owner.id, ref.id, exc)
            return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/addons/test_repository.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/infrastructure/addons/repository.py tests/infrastructure/addons/test_repository.py
git commit -m "feat(addons): add AddonRepository with fault isolation"
```

---

### Task 8: Application use cases

**Files:**
- Create: `src/gravitas/application/__init__.py`
- Create: `src/gravitas/application/install_addon.py`
- Create: `src/gravitas/application/browse_catalog.py`
- Create: `src/gravitas/application/get_detail.py`
- Create: `src/gravitas/application/resolve_stream.py`
- Create: `tests/application/test_use_cases.py`

**Interfaces:**
- `InstallAddon(repo: AddonRepository)`: `async __call__(url: str) -> AddonManifest`
- `CatalogRow` dataclass: `title: str`, `items: list[MediaItem]`
- `BrowseCatalog(repo: AddonRepository)`: `async __call__() -> list[CatalogRow]` (one row per catalog ref, title = `ref.name`)
- `GetDetail(repo: AddonRepository)`: `async __call__(manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail`
- `ResolveStream(repo: AddonRepository)`: `async __call__(manifest, type, id) -> list[Stream]` returning only `is_direct` streams; raises `NoStreams` if none direct.

Note: `GetDetail`/`ResolveStream` take an explicit `manifest` (the addon that owns the item). For the MVP this is the single installed addon.

- [ ] **Step 1: Write the failing test**

`tests/application/__init__.py`: (empty)

`tests/application/test_use_cases.py`:
```python
import pytest

from gravitas.application.browse_catalog import BrowseCatalog, CatalogRow
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import NoStreams
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)
from gravitas.infrastructure.addons.repository import AddonRepository


class FakeSource:
    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id="fake", name="Fake", version="1", resources=("catalog", "meta", "stream"),
            types=("movie",), catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
            base_url=url,
        )

    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        return [MediaItem(id="tt1", type="movie", name="A", poster=None)]

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        return MetaDetail(id=id, type=type, name="A", description="d",
                          poster=None, background=None, videos=())

    async def fetch_streams(self, manifest: AddonManifest, type: MediaType, id: str) -> list[Stream]:
        return [
            Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None),
            Stream(name="720p", title="torr", url=None, info_hash="abc", file_idx=0),
        ]


async def _repo() -> AddonRepository:
    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    return repo


async def test_install_addon() -> None:
    repo = AddonRepository(FakeSource())
    manifest = await InstallAddon(repo)("https://a/")
    assert manifest.id == "fake"


async def test_browse_catalog_builds_rows() -> None:
    repo = await _repo()
    rows = await BrowseCatalog(repo)()
    assert rows == [CatalogRow(title="Top", items=[MediaItem(id="tt1", type="movie", name="A", poster=None)])]


async def test_get_detail() -> None:
    repo = await _repo()
    manifest = repo.installed()[0]
    meta = await GetDetail(repo)(manifest, "movie", "tt1")
    assert meta.name == "A"


async def test_resolve_stream_filters_to_direct() -> None:
    repo = await _repo()
    manifest = repo.installed()[0]
    streams = await ResolveStream(repo)(manifest, "movie", "tt1")
    assert len(streams) == 1
    assert streams[0].is_direct


async def test_resolve_stream_raises_when_no_direct() -> None:
    class NoDirect(FakeSource):
        async def fetch_streams(self, manifest, type, id):  # type: ignore[no-untyped-def]
            return [Stream(name="x", title="t", url=None, info_hash="h", file_idx=0)]

    repo = AddonRepository(NoDirect())
    manifest = await repo.install("https://a/")
    with pytest.raises(NoStreams):
        await ResolveStream(repo)(manifest, "movie", "tt1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/application/test_use_cases.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/application/__init__.py`: (empty)

`src/gravitas/application/install_addon.py`:
```python
"""Use case: install an addon by manifest URL."""

from __future__ import annotations

from gravitas.domain.models import AddonManifest
from gravitas.infrastructure.addons.repository import AddonRepository


class InstallAddon:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, url: str) -> AddonManifest:
        return await self._repo.install(url)
```

`src/gravitas/application/browse_catalog.py`:
```python
"""Use case: build catalog rows across all installed addons."""

from __future__ import annotations

from dataclasses import dataclass

from gravitas.domain.models import MediaItem
from gravitas.infrastructure.addons.repository import AddonRepository


@dataclass(frozen=True, slots=True)
class CatalogRow:
    title: str
    items: list[MediaItem]


class BrowseCatalog:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self) -> list[CatalogRow]:
        rows: list[CatalogRow] = []
        for manifest, ref in self._repo.catalog_refs():
            items = await self._repo.aggregate_catalog(manifest, ref)
            rows.append(CatalogRow(title=ref.name, items=items))
        return rows
```

`src/gravitas/application/get_detail.py`:
```python
"""Use case: fetch full meta for one item."""

from __future__ import annotations

from gravitas.domain.models import AddonManifest, MediaType, MetaDetail
from gravitas.infrastructure.addons.repository import AddonRepository


class GetDetail:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> MetaDetail:
        return await self._repo._source.fetch_meta(manifest, type, id)
```

Note: `GetDetail` and `ResolveStream` need direct access to the source's `fetch_meta`/`fetch_streams`. Rather than reach into `repo._source`, add two thin pass-throughs on `AddonRepository` — update `repository.py` to add:
```python
    async def meta(self, manifest: AddonManifest, type: "MediaType", id: str) -> "MetaDetail":
        return await self._source.fetch_meta(manifest, type, id)

    async def streams(self, manifest: AddonManifest, type: "MediaType", id: str) -> list["Stream"]:
        return await self._source.fetch_streams(manifest, type, id)
```
and add `MediaType, MetaDetail, Stream` to `repository.py`'s imports from `gravitas.domain.models`. Then implement `GetDetail.__call__` as `return await self._repo.meta(manifest, type, id)`.

`src/gravitas/application/resolve_stream.py`:
```python
"""Use case: fetch streams and keep only directly-playable URLs (MVP: no torrents)."""

from __future__ import annotations

from gravitas.domain.errors import NoStreams
from gravitas.domain.models import AddonManifest, MediaType, Stream
from gravitas.infrastructure.addons.repository import AddonRepository


class ResolveStream:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        streams = await self._repo.streams(manifest, type, id)
        direct = [s for s in streams if s.is_direct]
        if not direct:
            raise NoStreams(f"no direct-URL streams for {id}")
        return direct
```

(Update `get_detail.py` to call `self._repo.meta(...)` per the note.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/application/test_use_cases.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/application/ src/gravitas/infrastructure/addons/repository.py tests/application/
git commit -m "feat(application): add install/browse/detail/resolve use cases"
```

---

### Task 9: DiskCache (poster/JSON byte cache)

**Files:**
- Create: `src/gravitas/infrastructure/cache/__init__.py`
- Create: `src/gravitas/infrastructure/cache/disk_cache.py`
- Create: `tests/infrastructure/cache/test_disk_cache.py`

**Interfaces:**
- Consumes: `Cache` port, httpx.
- Produces: `DiskCache(client: httpx.AsyncClient, root: Path)` implementing `Cache`. `get_or_fetch(url)` returns cached bytes if a file keyed by `sha256(url)` exists, else fetches via httpx, writes, returns. Transport failure raises `AddonUnreachable`.

- [ ] **Step 1: Write the failing test**

`tests/infrastructure/cache/__init__.py`: (empty)

`tests/infrastructure/cache/test_disk_cache.py`:
```python
from pathlib import Path

import httpx
import respx

from gravitas.infrastructure.cache.disk_cache import DiskCache


@respx.mock
async def test_fetches_then_caches(tmp_path: Path) -> None:
    route = respx.get("http://img/1.jpg").mock(
        return_value=httpx.Response(200, content=b"JPEGBYTES")
    )
    async with httpx.AsyncClient() as http:
        cache = DiskCache(http, root=tmp_path)
        first = await cache.get_or_fetch("http://img/1.jpg")
        second = await cache.get_or_fetch("http://img/1.jpg")
    assert first == b"JPEGBYTES"
    assert second == b"JPEGBYTES"
    assert route.call_count == 1  # second call served from disk
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/cache/test_disk_cache.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/infrastructure/cache/__init__.py`: (empty)

`src/gravitas/infrastructure/cache/disk_cache.py`:
```python
"""Content cache for posters/artwork keyed by URL hash."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from gravitas.domain.errors import AddonUnreachable


class DiskCache:
    def __init__(self, client: httpx.AsyncClient, root: Path) -> None:
        self._client = client
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._root / digest

    async def get_or_fetch(self, url: str) -> bytes:
        path = self._path_for(url)
        if path.exists():
            return path.read_bytes()
        try:
            response = await self._client.get(url, follow_redirects=True, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AddonUnreachable(f"GET {url} failed: {exc}") from exc
        data = response.content
        path.write_bytes(data)
        return data
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/cache/test_disk_cache.py -q`
Expected: pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/infrastructure/cache/ tests/infrastructure/cache/
git commit -m "feat(cache): add DiskCache for posters"
```

---

### Task 10: MpvPlayer (libmpv adapter)

**Files:**
- Create: `src/gravitas/infrastructure/player/__init__.py`
- Create: `src/gravitas/infrastructure/player/mpv_player.py`
- Create: `tests/infrastructure/player/test_mpv_player.py`

**Interfaces:**
- Consumes: `MediaPlayer` port, `PlaybackFailed`, python-mpv.
- Produces: `MpvPlayer(window_id: int)` implementing `MediaPlayer`. Constructs `mpv.MPV(wid=str(window_id), ...)`. `subtitle_tracks()` reads mpv's `track-list` and returns `(id, title)` for `type == "sub"`. `set_subtitle_track(None)` sets `sid="no"`; an int sets `sid` to that id. `shutdown()` calls `terminate()`.
- Because libmpv requires a display + native window, the unit test injects a fake mpv object via a `factory` seam: `MpvPlayer(window_id, factory=lambda wid: FakeMpv())`.

- [ ] **Step 1: Write the failing test**

`tests/infrastructure/player/__init__.py`: (empty)

`tests/infrastructure/player/test_mpv_player.py`:
```python
from typing import Any

from gravitas.infrastructure.player.mpv_player import MpvPlayer


class FakeMpv:
    def __init__(self) -> None:
        self.props: dict[str, Any] = {}
        self.played: list[str] = []
        self.terminated = False
        self.track_list = [
            {"id": 1, "type": "video", "title": "v"},
            {"id": 2, "type": "sub", "title": "English"},
            {"id": 3, "type": "sub", "title": "Spanish"},
        ]

    def play(self, url: str) -> None:
        self.played.append(url)

    def __setitem__(self, key: str, value: Any) -> None:
        self.props[key] = value

    def __getattr__(self, name: str) -> Any:
        if name == "track_list":
            return object.__getattribute__(self, "__dict__")["track_list"]
        raise AttributeError(name)

    def terminate(self) -> None:
        self.terminated = True


def _player() -> tuple[MpvPlayer, FakeMpv]:
    fake = FakeMpv()
    player = MpvPlayer(window_id=42, factory=lambda wid: fake)
    return player, fake


def test_play_forwards_url() -> None:
    player, fake = _player()
    player.play("http://s/v.mkv")
    assert fake.played == ["http://s/v.mkv"]


def test_subtitle_tracks_lists_only_subs() -> None:
    player, _ = _player()
    assert player.subtitle_tracks() == [(2, "English"), (3, "Spanish")]


def test_set_subtitle_track() -> None:
    player, fake = _player()
    player.set_subtitle_track(3)
    assert fake.props["sid"] == 3
    player.set_subtitle_track(None)
    assert fake.props["sid"] == "no"


def test_shutdown_terminates() -> None:
    player, fake = _player()
    player.shutdown()
    assert fake.terminated is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/player/test_mpv_player.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/infrastructure/player/__init__.py`: (empty)

`src/gravitas/infrastructure/player/mpv_player.py`:
```python
"""MediaPlayer implementation embedding libmpv into a native window (by window id)."""

from __future__ import annotations

import locale
from collections.abc import Callable
from typing import Any

import mpv

from gravitas.domain.errors import PlaybackFailed

# libmpv needs the C numeric locale; Qt may have changed it.
locale.setlocale(locale.LC_NUMERIC, "C")

MpvFactory = Callable[[int], Any]


def _default_factory(window_id: int) -> Any:
    return mpv.MPV(
        wid=str(window_id),
        vo="gpu",
        hwdec="auto-safe",
        osc=False,
        input_default_bindings=False,
    )


class MpvPlayer:
    def __init__(self, window_id: int, factory: MpvFactory = _default_factory) -> None:
        try:
            self._mpv = factory(window_id)
        except Exception as exc:  # noqa: BLE001 - surface any libmpv init failure uniformly
            raise PlaybackFailed(f"failed to initialise libmpv: {exc}") from exc

    def play(self, url: str) -> None:
        try:
            self._mpv.play(url)
        except Exception as exc:  # noqa: BLE001
            raise PlaybackFailed(f"failed to play {url}: {exc}") from exc

    def pause(self) -> None:
        self._mpv["pause"] = True

    def resume(self) -> None:
        self._mpv["pause"] = False

    def seek(self, seconds: float) -> None:
        self._mpv.seek(seconds, reference="absolute")

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        tracks: list[tuple[int, str]] = []
        for track in self._mpv.track_list:
            if track.get("type") == "sub":
                tracks.append((int(track["id"]), str(track.get("title") or f"Track {track['id']}")))
        return tracks

    def set_subtitle_track(self, track_id: int | None) -> None:
        self._mpv["sid"] = "no" if track_id is None else track_id

    def shutdown(self) -> None:
        self._mpv.terminate()
```

Note: the fake's `seek` is not exercised in tests to avoid coupling to mpv's signature; `pause`/`resume`/`seek` are covered by the controller state test in Task 11 via a fake `MediaPlayer`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/infrastructure/player/test_mpv_player.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/infrastructure/player/ tests/infrastructure/player/
git commit -m "feat(player): add libmpv MediaPlayer adapter"
```

---

### Task 11: Presentation models (QAbstractListModel)

**Files:**
- Create: `src/gravitas/presentation/__init__.py`
- Create: `src/gravitas/presentation/models/__init__.py`
- Create: `src/gravitas/presentation/models/poster_grid_model.py`
- Create: `src/gravitas/presentation/models/stream_list_model.py`
- Create: `tests/presentation/test_models.py`

**Interfaces:**
- `PosterGridModel(QAbstractListModel)`: roles `IdRole`, `TypeRole`, `NameRole`, `PosterRole`; `set_items(items: list[MediaItem])`; `item_at(row: int) -> MediaItem`.
- `StreamListModel(QAbstractListModel)`: roles `NameRole`, `TitleRole`, `UrlRole`; `set_streams(streams: list[Stream])`; `stream_at(row: int) -> Stream`.
- Tests use `pytest-qt`'s `qapp` fixture (a `QApplication` must exist to build models).

- [ ] **Step 1: Write the failing test**

`tests/presentation/__init__.py`: (empty)

`tests/presentation/test_models.py`:
```python
from PySide6.QtCore import Qt

from gravitas.domain.models import MediaItem, Stream
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.stream_list_model import StreamListModel


def test_poster_model_exposes_rows_and_roles(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([MediaItem(id="tt1", type="movie", name="Film", poster="http://p/1.jpg")])
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, PosterGridModel.NameRole) == "Film"
    assert model.data(index, PosterGridModel.PosterRole) == "http://p/1.jpg"
    assert model.item_at(0).id == "tt1"


def test_poster_model_role_names_are_stringified(qapp: object) -> None:
    model = PosterGridModel()
    names = {bytes(v).decode() for v in model.roleNames().values()}
    assert {"id", "type", "name", "poster"} <= names


def test_stream_model(qapp: object) -> None:
    model = StreamListModel()
    model.set_streams([
        Stream(name="1080p", title="web", url="http://s/v.mkv", info_hash=None, file_idx=None),
    ])
    index = model.index(0, 0)
    assert model.data(index, StreamListModel.NameRole) == "1080p"
    assert model.stream_at(0).url == "http://s/v.mkv"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/presentation/__init__.py`, `src/gravitas/presentation/models/__init__.py`: (empty)

`src/gravitas/presentation/models/poster_grid_model.py`:
```python
"""Qt list model exposing MediaItems to the QML poster grid."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from gravitas.domain.models import MediaItem


class PosterGridModel(QAbstractListModel):
    IdRole = Qt.ItemDataRole.UserRole + 1
    TypeRole = Qt.ItemDataRole.UserRole + 2
    NameRole = Qt.ItemDataRole.UserRole + 3
    PosterRole = Qt.ItemDataRole.UserRole + 4

    def __init__(self) -> None:
        super().__init__()
        self._items: list[MediaItem] = []

    def set_items(self, items: list[MediaItem]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def item_at(self, row: int) -> MediaItem:
        return self._items[row]

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        item = self._items[index.row()]
        match role:
            case PosterGridModel.IdRole:
                return item.id
            case PosterGridModel.TypeRole:
                return item.type
            case PosterGridModel.NameRole:
                return item.name
            case PosterGridModel.PosterRole:
                return item.poster
        return None

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return {
            PosterGridModel.IdRole: b"id",
            PosterGridModel.TypeRole: b"type",
            PosterGridModel.NameRole: b"name",
            PosterGridModel.PosterRole: b"poster",
        }
```

`src/gravitas/presentation/models/stream_list_model.py`:
```python
"""Qt list model exposing resolved streams to the QML source picker."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from gravitas.domain.models import Stream


class StreamListModel(QAbstractListModel):
    NameRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    UrlRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self) -> None:
        super().__init__()
        self._streams: list[Stream] = []

    def set_streams(self, streams: list[Stream]) -> None:
        self.beginResetModel()
        self._streams = list(streams)
        self.endResetModel()

    def stream_at(self, row: int) -> Stream:
        return self._streams[row]

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(self._streams)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        stream = self._streams[index.row()]
        match role:
            case StreamListModel.NameRole:
                return stream.name
            case StreamListModel.TitleRole:
                return stream.title
            case StreamListModel.UrlRole:
                return stream.url
        return None

    def roleNames(self) -> dict[int, bytes]:  # noqa: N802
        return {
            StreamListModel.NameRole: b"name",
            StreamListModel.TitleRole: b"title",
            StreamListModel.UrlRole: b"url",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/presentation/test_models.py -q`
Expected: all pass (pytest-qt provides `qapp`).
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/__init__.py src/gravitas/presentation/models/ tests/presentation/
git commit -m "feat(presentation): add poster/stream list models"
```

---

### Task 12: Controllers (QObject bridges)

**Files:**
- Create: `src/gravitas/presentation/controllers/__init__.py`
- Create: `src/gravitas/presentation/controllers/catalog_controller.py`
- Create: `src/gravitas/presentation/controllers/detail_controller.py`
- Create: `src/gravitas/presentation/controllers/player_controller.py`
- Create: `tests/presentation/test_player_controller.py`

**Interfaces:**
- `PlayerController(player: MediaPlayer)` (QObject): slot `play(url: str)`, `pause()`, `resume()`, `seek(seconds: float)`, `selectSubtitle(track_id: int)`; property/method `subtitleTracks() -> list` (list of `{"id", "title"}` dicts); signal `errorOccurred(str)`. On `PlaybackFailed`, emit `errorOccurred` instead of raising.
- `CatalogController(browse: BrowseCatalog, model: PosterGridModel)` (QObject): async slot `refresh()` populating the model with the first row's items (MVP: flatten first catalog); signal `errorOccurred(str)`. (Async slot driven by qasync.)
- `DetailController(get_detail, resolve_stream, stream_model)` (QObject): async slot `load(manifest, type, id)` and `resolve(...)`; signal `errorOccurred(str)`.
- Only `PlayerController` is unit-tested here (pure state machine with a fake `MediaPlayer`). Catalog/Detail controllers are exercised via the app-launch verification in Task 14 (they are thin async wrappers over already-tested use cases).

- [ ] **Step 1: Write the failing test**

`tests/presentation/test_player_controller.py`:
```python
from gravitas.presentation.controllers.player_controller import PlayerController


class FakePlayer:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []
        self.sub: int | None = -1

    def play(self, url: str) -> None:
        if self.fail:
            from gravitas.domain.errors import PlaybackFailed

            raise PlaybackFailed("boom")
        self.calls.append(f"play:{url}")

    def pause(self) -> None:
        self.calls.append("pause")

    def resume(self) -> None:
        self.calls.append("resume")

    def seek(self, seconds: float) -> None:
        self.calls.append(f"seek:{seconds}")

    def set_subtitle_track(self, track_id: int | None) -> None:
        self.sub = track_id

    def subtitle_tracks(self) -> list[tuple[int, str]]:
        return [(2, "English")]

    def shutdown(self) -> None:
        self.calls.append("shutdown")


def test_play_and_controls(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(player)
    controller.play("http://s/v.mkv")
    controller.pause()
    controller.resume()
    controller.seek(30.0)
    assert player.calls == ["play:http://s/v.mkv", "pause", "resume", "seek:30.0"]


def test_subtitle_tracks_exposed_as_dicts(qapp: object) -> None:
    controller = PlayerController(FakePlayer())
    assert controller.subtitleTracks() == [{"id": 2, "title": "English"}]


def test_select_subtitle(qapp: object) -> None:
    player = FakePlayer()
    controller = PlayerController(player)
    controller.selectSubtitle(2)
    assert player.sub == 2


def test_playback_error_emits_signal(qapp: object) -> None:
    player = FakePlayer(fail=True)
    controller = PlayerController(player)
    received: list[str] = []
    controller.errorOccurred.connect(received.append)
    controller.play("http://s/v.mkv")
    assert received == ["boom"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/presentation/test_player_controller.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

`src/gravitas/presentation/controllers/__init__.py`: (empty)

`src/gravitas/presentation/controllers/player_controller.py`:
```python
"""QObject bridge exposing playback controls to QML."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.ports import MediaPlayer


class PlayerController(QObject):
    errorOccurred = Signal(str)

    def __init__(self, player: MediaPlayer) -> None:
        super().__init__()
        self._player = player

    @Slot(str)
    def play(self, url: str) -> None:
        try:
            self._player.play(url)
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))

    @Slot()
    def pause(self) -> None:
        self._player.pause()

    @Slot()
    def resume(self) -> None:
        self._player.resume()

    @Slot(float)
    def seek(self, seconds: float) -> None:
        self._player.seek(seconds)

    @Slot(int)
    def selectSubtitle(self, track_id: int) -> None:
        self._player.set_subtitle_track(None if track_id < 0 else track_id)

    @Slot(result="QVariantList")
    def subtitleTracks(self) -> list[dict[str, object]]:
        return [{"id": tid, "title": title} for tid, title in self._player.subtitle_tracks()]
```

`src/gravitas/presentation/controllers/catalog_controller.py`:
```python
"""QObject bridge: run BrowseCatalog and populate the poster grid model."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.domain.errors import GravitasError
from gravitas.presentation.models.poster_grid_model import PosterGridModel


class CatalogController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)

    def __init__(self, browse: BrowseCatalog, model: PosterGridModel) -> None:
        super().__init__()
        self._browse = browse
        self._model = model

    @Slot()
    async def refresh(self) -> None:
        self.loadingChanged.emit(True)
        try:
            rows = await self._browse()
            items = [item for row in rows for item in row.items]
            self._model.set_items(items)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
        finally:
            self.loadingChanged.emit(False)
```

`src/gravitas/presentation/controllers/detail_controller.py`:
```python
"""QObject bridge: load meta and resolve streams for a selected item."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from gravitas.application.get_detail import GetDetail
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import AddonManifest, MediaType
from gravitas.presentation.models.stream_list_model import StreamListModel


class DetailController(QObject):
    errorOccurred = Signal(str)
    titleChanged = Signal(str)
    descriptionChanged = Signal(str)

    def __init__(
        self,
        get_detail: GetDetail,
        resolve_stream: ResolveStream,
        stream_model: StreamListModel,
    ) -> None:
        super().__init__()
        self._get_detail = get_detail
        self._resolve_stream = resolve_stream
        self._stream_model = stream_model
        self._manifest: AddonManifest | None = None

    def bind_manifest(self, manifest: AddonManifest) -> None:
        self._manifest = manifest

    @Slot(str, str)
    async def load(self, type: str, item_id: str) -> None:
        if self._manifest is None:
            self.errorOccurred.emit("no addon installed")
            return
        media_type: MediaType = "series" if type == "series" else "movie"
        try:
            meta = await self._get_detail(self._manifest, media_type, item_id)
            self.titleChanged.emit(meta.name)
            self.descriptionChanged.emit(meta.description or "")
            streams = await self._resolve_stream(self._manifest, media_type, item_id)
            self._stream_model.set_streams(streams)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/presentation/test_player_controller.py -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check .`
Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add src/gravitas/presentation/controllers/ tests/presentation/test_player_controller.py
git commit -m "feat(presentation): add catalog/detail/player controllers"
```

---

### Task 13: QML UI

**Files:**
- Create: `src/gravitas/presentation/qml/Main.qml`
- Create: `src/gravitas/presentation/qml/Home.qml`
- Create: `src/gravitas/presentation/qml/Detail.qml`
- Create: `src/gravitas/presentation/qml/Player.qml`
- Create: `src/gravitas/presentation/qml/components/PosterCard.qml`
- Create: `src/gravitas/presentation/qml/components/StreamRow.qml`

**Interfaces:**
- Consumes context properties set in Task 14: `catalogController`, `detailController`, `playerController`, `posterModel`, `streamModel`.
- Produces: the visible UI. QML has no unit test here; it is verified by launching the app in Task 14.

- [ ] **Step 1: Write `PosterCard.qml`**

```qml
import QtQuick
import QtQuick.Controls

Item {
    id: root
    property string title
    property string posterUrl
    signal clicked()
    width: 160; height: 260

    Column {
        spacing: 6
        anchors.fill: parent
        Rectangle {
            width: 160; height: 220; radius: 8; color: "#222"
            clip: true
            Image {
                anchors.fill: parent
                source: root.posterUrl ? root.posterUrl : ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
            }
            MouseArea { anchors.fill: parent; onClicked: root.clicked() }
        }
        Text {
            width: 160; text: root.title; color: "white"
            elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WordWrap
        }
    }
}
```

- [ ] **Step 2: Write `StreamRow.qml`**

```qml
import QtQuick

Rectangle {
    id: root
    property string name
    property string subtitle
    signal clicked()
    height: 56; radius: 6
    color: mouse.containsMouse ? "#333" : "#1c1c1c"

    Column {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left; anchors.leftMargin: 12
        Text { text: root.name; color: "white"; font.bold: true }
        Text { text: root.subtitle; color: "#aaa"; font.pixelSize: 12 }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.clicked() }
}
```

- [ ] **Step 3: Write `Home.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    signal openDetail(string type, string id)

    Component.onCompleted: catalogController.refresh()

    GridView {
        anchors.fill: parent
        anchors.margins: 24
        cellWidth: 180; cellHeight: 280
        model: posterModel
        delegate: PosterCard {
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            onClicked: home.openDetail(model.type, model.id)
        }
    }

    BusyIndicator {
        anchors.centerIn: parent
        running: false
        Connections {
            target: catalogController
            function onLoadingChanged(loading) { parent.running = loading }
        }
    }
}
```

- [ ] **Step 4: Write `Detail.qml`**

```qml
import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: detail
    property string mediaType
    property string mediaId
    signal playUrl(string url)

    onMediaIdChanged: if (mediaId.length) detailController.load(mediaType, mediaId)

    property string title: ""
    property string description: ""
    Connections {
        target: detailController
        function onTitleChanged(t) { detail.title = t }
        function onDescriptionChanged(d) { detail.description = d }
    }

    Column {
        anchors.fill: parent; anchors.margins: 24; spacing: 12
        Text { text: detail.title; color: "white"; font.pixelSize: 28; font.bold: true }
        Text {
            text: detail.description; color: "#ccc"; width: parent.width
            wrapMode: Text.WordWrap
        }
        Text { text: "Sources"; color: "white"; font.pixelSize: 20 }
        ListView {
            width: parent.width; height: 300; spacing: 8
            model: streamModel
            delegate: StreamRow {
                width: ListView.view.width
                name: model.name
                subtitle: model.title
                onClicked: if (model.url) detail.playUrl(model.url)
            }
        }
    }
}
```

- [ ] **Step 5: Write `Player.qml`**

```qml
import QtQuick
import QtQuick.Controls

Item {
    id: player
    property string url
    signal back()

    onUrlChanged: if (url.length) playerController.play(url)

    // libmpv renders into the native window behind this transparent surface.
    Rectangle { anchors.fill: parent; color: "transparent" }

    Row {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: 24
        spacing: 12
        Button { text: "Pause"; onClicked: playerController.pause() }
        Button { text: "Resume"; onClicked: playerController.resume() }
        ComboBox {
            id: subs
            textRole: "title"
            model: playerController.subtitleTracks()
            onActivated: playerController.selectSubtitle(model[currentIndex].id)
        }
        Button { text: "Back"; onClicked: player.back() }
    }
}
```

- [ ] **Step 6: Write `Main.qml`**

```qml
import QtQuick
import QtQuick.Controls

ApplicationWindow {
    id: window
    visible: true
    width: 1280; height: 800
    title: "Gravitas"
    color: "#141414"

    StackView {
        id: stack
        anchors.fill: parent
        initialItem: homePage
    }

    Component {
        id: homePage
        Home { onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id}) }
    }
    Component {
        id: detailPage
        Detail { onPlayUrl: (url) => stack.push(playerPage, {url: url}) }
    }
    Component {
        id: playerPage
        Player { onBack: stack.pop() }
    }

    Connections {
        target: catalogController
        function onErrorOccurred(msg) { errorBar.show(msg) }
    }
    Connections {
        target: detailController
        function onErrorOccurred(msg) { errorBar.show(msg) }
    }

    Rectangle {
        id: errorBar
        function show(msg) { label.text = msg; visible = true; hideTimer.restart() }
        visible: false
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 40; color: "#902020"; z: 100
        Text { id: label; anchors.centerIn: parent; color: "white" }
        Timer { id: hideTimer; interval: 4000; onTriggered: errorBar.visible = false }
    }
}
```

- [ ] **Step 7: Verify QML parses**

Run: `uv run python -c "from PySide6.QtQml import QQmlEngine; e=QQmlEngine(); from PySide6.QtCore import QUrl; import PySide6.QtQuick"`
Expected: no error (import smoke; full render verified in Task 14).

- [ ] **Step 8: Commit**

```bash
git add src/gravitas/presentation/qml/
git commit -m "feat(ui): add QML home/detail/player screens"
```

---

### Task 14: Composition root + end-to-end launch

**Files:**
- Create: `src/gravitas/main.py`
- Create: `tests/test_composition.py`
- Modify: `pyproject.toml` (add `[project.scripts]` entry point)

**Interfaces:**
- Consumes: everything.
- Produces: `build_app(argv, default_addon_url) -> tuple[QGuiApplication, QQmlApplicationEngine]` and `main() -> int`. `build_app` wires: one `httpx.AsyncClient`, `AddonClient`, `AddonRepository`, use cases, models, controllers; registers them as QML context properties; loads `Main.qml`. It installs the default Cinemeta addon at startup (async, kicked off on the qasync loop) and binds its manifest into the `DetailController`.
- The window id for `MpvPlayer` comes from the player surface; for MVP the player is embedded by passing the QML window's `winId()` after the window is shown. Wiring detail: create `MpvPlayer` lazily on first `playerController.play`, using `window.winId()`. Expose this via a small factory the `PlayerController` calls — update `PlayerController` to accept `player_factory: Callable[[], MediaPlayer]` instead of an eager `MediaPlayer`, constructing on first `play`. (See Step 3.)

- [ ] **Step 1: Write the failing test**

`tests/test_composition.py`:
```python
from gravitas.main import build_app


def test_build_app_registers_context_properties(qapp: object) -> None:
    app, engine = build_app(argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json")
    ctx = engine.rootContext()
    assert ctx.contextProperty("catalogController") is not None
    assert ctx.contextProperty("detailController") is not None
    assert ctx.contextProperty("playerController") is not None
    assert ctx.contextProperty("posterModel") is not None
    assert ctx.contextProperty("streamModel") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_composition.py -q`
Expected: FAIL with `ModuleNotFoundError: gravitas.main`.

- [ ] **Step 3: Update `PlayerController` to lazy-construct the player**

Edit `src/gravitas/presentation/controllers/player_controller.py`: replace the constructor and `play` slot so the player is built on first use via a factory. New top and methods:
```python
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

from gravitas.domain.errors import PlaybackFailed
from gravitas.domain.ports import MediaPlayer


class PlayerController(QObject):
    errorOccurred = Signal(str)

    def __init__(self, player_factory: Callable[[], MediaPlayer]) -> None:
        super().__init__()
        self._factory = player_factory
        self._player: MediaPlayer | None = None

    def _ensure(self) -> MediaPlayer | None:
        if self._player is None:
            try:
                self._player = self._factory()
            except PlaybackFailed as exc:
                self.errorOccurred.emit(str(exc))
                return None
        return self._player

    @Slot(str)
    def play(self, url: str) -> None:
        player = self._ensure()
        if player is None:
            return
        try:
            player.play(url)
        except PlaybackFailed as exc:
            self.errorOccurred.emit(str(exc))
```
Keep `pause`/`resume`/`seek`/`selectSubtitle`/`subtitleTracks`, but guard each with `if self._player is None: return` (for `subtitleTracks`, return `[]`). Update `tests/presentation/test_player_controller.py` to pass a factory: `PlayerController(lambda: player)`, and for the subtitle-tracks test call `controller.play("x")` first so the player exists (or assert `[]` before play). Re-run `uv run pytest tests/presentation/test_player_controller.py -q` and confirm green before continuing.

- [ ] **Step 4: Write `main.py`**

`src/gravitas/main.py`:
```python
"""Composition root: wire adapters into use cases and launch the QML app."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import qasync
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.ports import MediaPlayer
from gravitas.infrastructure.addons.client import AddonClient
from gravitas.infrastructure.addons.repository import AddonRepository
from gravitas.infrastructure.player.mpv_player import MpvPlayer
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.controllers.player_controller import PlayerController
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.stream_list_model import StreamListModel

_QML_DIR = Path(__file__).parent / "presentation" / "qml"
DEFAULT_ADDON = "https://v3-cinemeta.strem.io/manifest.json"


def build_app(
    argv: list[str], default_addon_url: str
) -> tuple[QGuiApplication, QQmlApplicationEngine]:
    app = QGuiApplication.instance() or QGuiApplication(argv)

    http = httpx.AsyncClient()
    source = AddonClient(http)
    repo = AddonRepository(source)

    poster_model = PosterGridModel()
    stream_model = StreamListModel()

    catalog_controller = CatalogController(BrowseCatalog(repo), poster_model)
    detail_controller = DetailController(GetDetail(repo), ResolveStream(repo), stream_model)

    engine = QQmlApplicationEngine()

    def make_player() -> MediaPlayer:
        root_objects = engine.rootObjects()
        window_id = int(root_objects[0].winId()) if root_objects else 0
        return MpvPlayer(window_id=window_id)

    player_controller = PlayerController(make_player)

    ctx = engine.rootContext()
    ctx.setContextProperty("catalogController", catalog_controller)
    ctx.setContextProperty("detailController", detail_controller)
    ctx.setContextProperty("playerController", player_controller)
    ctx.setContextProperty("posterModel", poster_model)
    ctx.setContextProperty("streamModel", stream_model)

    async def bootstrap() -> None:
        manifest = await InstallAddon(repo)(default_addon_url)
        detail_controller.bind_manifest(manifest)

    engine.load(str(_QML_DIR / "Main.qml"))
    if engine.rootObjects():
        asyncio.ensure_future(bootstrap())
    # keep a reference so the client isn't GC'd
    app.setProperty("_http_client_alive", True)
    engine._http = http  # type: ignore[attr-defined]
    return app, engine


def main() -> int:
    app = QGuiApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    _, engine = build_app(sys.argv, DEFAULT_ADDON)
    if not engine.rootObjects():
        return 1
    with loop:
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Add the entry point to `pyproject.toml`**

Add under `[project]` section:
```toml
[project.scripts]
gravitas = "gravitas.main:main"
```

- [ ] **Step 6: Run the composition test**

Run: `uv run pytest tests/test_composition.py -q`
Expected: pass.
Then full suite: `uv run pytest -q`
Expected: all pass.
Then: `uv run mypy src && uv run ruff check . && uv run ruff format --check .`
Expected: all pass.

- [ ] **Step 7: Manual end-to-end launch (verification)**

Precondition: a graphical session (X11/Wayland) and libmpv installed system-wide (`mpv`/`libmpv` package). Use a **direct-URL** test addon or Cinemeta plus a direct-URL stream addon.

Run: `uv run gravitas`
Expected:
1. Window opens titled "Gravitas".
2. Cinemeta posters load into the grid within a few seconds.
3. Click a poster → detail page shows title + description + a "Sources" list.
4. Click a direct-URL source → player screen; video renders; Pause/Resume work; subtitle ComboBox lists tracks and switching changes subtitles; Back returns to detail.

If posters do not load, confirm network + that Cinemeta returned `metas`. If video is black, confirm libmpv is installed and the window id was passed (check logs). Record the result of this manual check in the commit message.

- [ ] **Step 8: Commit**

```bash
git add src/gravitas/main.py src/gravitas/presentation/controllers/player_controller.py tests/test_composition.py tests/presentation/test_player_controller.py pyproject.toml
git commit -m "feat: wire composition root and launch end-to-end"
```

---

## Self-Review Notes

- **Spec coverage:** addon install (T6/T8), browse catalog + poster grid (T8/T11/T13), detail + episodes (T3/T8/T13), stream picker (T8/T11/T13), direct-URL playback + subtitle selector (T10/T12/T13), addon-provided metadata (T5/T6), fault isolation (T7), disk poster cache (T9), Clean Architecture layering (all tasks), uv/ruff/mypy-strict gates (T1 + every task). Cinemeta default addon (T14). Torrent-free + debrid-later (ports T4, `DebridResolver` stub unused). All spec items map to a task.
- **Out-of-scope items** (search, Trakt, themes, continue-watching, spoiler-blur, TMDb/TVDB, client-side debrid, animations) are intentionally deferred per the spec's milestone list; not planned here.
- **Type consistency:** `MediaType`, `AddonManifest`, `CatalogRef`, `MediaItem`, `MetaDetail`, `Stream`, `Video` names are stable across tasks. `AddonRepository` gains `meta()`/`streams()` pass-throughs in T8 (noted inline). `PlayerController` is refactored from eager player to `player_factory` in T14 (noted inline with test updates).
- **Known real-world caveat:** MVP requires an addon that returns **direct-URL** streams to demonstrate playback (Cinemeta provides catalogs/meta; pair with a direct-URL stream addon). Torrent/infoHash streams are filtered out by `ResolveStream` and become playable only after the later client-side debrid milestone.
```
