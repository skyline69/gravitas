# Discover Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Home's "See All" open a Discover board — a full, filterable (Type / Catalog / Genre), infinitely scrolling poster grid for one catalog.

**Architecture:** Extend the infra to parse catalog `extra` (genre options, `skip`) and fetch a catalog with `genre`/`skip` params; add a `BrowseBoard` use case returning one page + `has_more`; add a `DiscoverController` driving a `PosterGridModel` (now with `append_items`) and exposing dropdown option lists as notifying properties; add `Discover.qml` pushed onto the StackView from `See All`. The board is single-catalog (no cross-addon aggregation).

**Tech Stack:** Python 3.13, PySide6 (Qt6/QML), `qasync`, `httpx`, `respx` (client tests), `pytest` (+ pytest-qt `qapp`), `ruff`, `mypy --strict`, `uv`.

## Global Constraints

- Clean Architecture dependency rule: `presentation → application → domain ← infrastructure`. `domain` imports no Qt/httpx. `application` never imports `infrastructure`. `infrastructure` never imports `application`/`presentation`.
- Gates on every commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` (strict, `src` only), `uv run pytest -q`. Test output pristine (no stray warnings).
- Untyped third-party (`qasync`, `PySide6` Property/Slot decorators) uses precise `# type: ignore[<code>]` — never blanket. If a decorator's exact code differs from what this plan wrote, use the code mypy actually reports.
- Async controller slots use `@qasync.asyncSlot`, never plain `@Slot` (a plain `@Slot` async method is silently dropped).
- QML context properties need a Python keep-alive (`engine._gravitas_refs`) or they GC to null.
- Page size for pagination is 100 (`BrowseBoard.PAGE_SIZE`).
- Genre value `None` means "All" (fetch with no `genre` param).
- The board is one catalog: errors propagate to the controller (surfaced via `errorOccurred`), unlike Home's fault-isolated `aggregate_catalog`.
- No new network calls on the Home path (existing `fetch_catalog` callers keep working via defaulted params).
- No Claude attribution in commit messages. All commands via `uv`.

---

### Task 1: Catalog `extra` metadata on `CatalogRef` + manifest parsing

**Files:**
- Modify: `src/gravitas/domain/models.py`
- Modify: `src/gravitas/infrastructure/addons/parsing.py`
- Test: `tests/infrastructure/addons/test_parsing.py`, `tests/domain/test_models.py`

**Interfaces:**
- Produces: `CatalogRef(type, id, name, genres: tuple[str,...] = (), supports_skip: bool = False)`; `parsing._parse_catalog_extra(raw) -> tuple[tuple[str,...], bool]` populating them.

- [ ] **Step 1: Write failing tests**

Add to `tests/infrastructure/addons/test_parsing.py`:

```python
def test_parse_manifest_reads_modern_extra() -> None:
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {
                "type": "movie",
                "id": "top",
                "name": "Top",
                "extra": [
                    {"name": "genre", "options": ["Action", "Comedy"]},
                    {"name": "skip"},
                ],
            }
        ],
    }
    m = parse_manifest(data, base_url="https://x/")
    ref = m.catalogs[0]
    assert ref.genres == ("Action", "Comedy")
    assert ref.supports_skip is True


def test_parse_manifest_reads_legacy_extra() -> None:
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {
                "type": "movie",
                "id": "top",
                "name": "Top",
                "extraSupported": ["genre", "skip"],
                "genres": ["Drama"],
            }
        ],
    }
    ref = parse_manifest(data, base_url="https://x/").catalogs[0]
    assert ref.genres == ("Drama",)
    assert ref.supports_skip is True


def test_parse_manifest_extra_absent_defaults() -> None:
    data = {"id": "x", "name": "X", "catalogs": [{"type": "movie", "id": "top", "name": "Top"}]}
    ref = parse_manifest(data, base_url="https://x/").catalogs[0]
    assert ref.genres == ()
    assert ref.supports_skip is False
```

Add to `tests/domain/test_models.py`:

```python
def test_catalog_ref_extra_defaults() -> None:
    from gravitas.domain.models import CatalogRef

    ref = CatalogRef(type="movie", id="top", name="Top")
    assert ref.genres == ()
    assert ref.supports_skip is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py tests/domain/test_models.py -q`
Expected: FAIL — `CatalogRef` has no `genres`/`supports_skip`.

- [ ] **Step 3: Extend `CatalogRef`**

In `src/gravitas/domain/models.py`, replace the `CatalogRef` dataclass:

```python
@dataclass(frozen=True, slots=True)
class CatalogRef:
    type: MediaType
    id: str
    name: str
    genres: tuple[str, ...] = ()
    supports_skip: bool = False
```

- [ ] **Step 4: Parse `extra` in `parse_manifest`**

In `src/gravitas/infrastructure/addons/parsing.py`, add the helper (above `parse_manifest`):

```python
def _parse_catalog_extra(raw: dict[str, Any]) -> tuple[tuple[str, ...], bool]:
    extra = raw.get("extra")
    if isinstance(extra, list):
        genres: tuple[str, ...] = ()
        supports_skip = False
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == "genre":
                genres = tuple(str(o) for o in entry.get("options", []))
            elif entry.get("name") == "skip":
                supports_skip = True
        return genres, supports_skip
    supported = raw.get("extraSupported")
    if isinstance(supported, list):
        genres = tuple(str(g) for g in raw.get("genres", [])) if "genre" in supported else ()
        return genres, "skip" in supported
    return (), False
```

And populate the fields in the catalog loop of `parse_manifest` (replace the `catalogs.append(...)` call):

```python
        genres, supports_skip = _parse_catalog_extra(raw)
        catalogs.append(
            CatalogRef(
                type=c_type,
                id=raw.get("id", ""),
                name=raw.get("name", raw.get("id", "")),
                genres=genres,
                supports_skip=supports_skip,
            )
        )
```

- [ ] **Step 5: Run tests, gates, commit**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py tests/domain/test_models.py -q` → PASS.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` → pass.

```bash
git add src/gravitas/domain/models.py src/gravitas/infrastructure/addons/parsing.py tests/infrastructure/addons/test_parsing.py tests/domain/test_models.py
git commit -m "feat(catalog): parse genre options + skip support from manifest extra"
```

---

### Task 2: `catalog_path_extra` pure function

**Files:**
- Modify: `src/gravitas/infrastructure/addons/parsing.py`
- Test: `tests/infrastructure/addons/test_parsing.py`

**Interfaces:**
- Consumes: `catalog_path(ref)` (existing).
- Produces: `catalog_path_extra(ref: CatalogRef, genre: str | None, skip: int) -> str`.

- [ ] **Step 1: Write failing test**

Add to `tests/infrastructure/addons/test_parsing.py` (and add `catalog_path_extra` to the existing import from `gravitas.infrastructure.addons.parsing`):

```python
def test_catalog_path_extra() -> None:
    from gravitas.infrastructure.addons.parsing import catalog_path_extra

    ref = CatalogRef(type="movie", id="top", name="T")
    assert catalog_path_extra(ref, None, 0) == "catalog/movie/top.json"
    assert catalog_path_extra(ref, "Action", 0) == "catalog/movie/top/genre=Action.json"
    assert catalog_path_extra(ref, None, 100) == "catalog/movie/top/skip=100.json"
    assert catalog_path_extra(ref, "Action", 100) == "catalog/movie/top/genre=Action&skip=100.json"
    assert catalog_path_extra(ref, "Sci-Fi & Fantasy", 0) == "catalog/movie/top/genre=Sci-Fi%20%26%20Fantasy.json"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py::test_catalog_path_extra -q`
Expected: FAIL — `catalog_path_extra` not defined.

- [ ] **Step 3: Implement**

In `src/gravitas/infrastructure/addons/parsing.py`, add the import at the top (with the other stdlib imports):

```python
from urllib.parse import quote
```

And add the function next to `catalog_path`:

```python
def catalog_path_extra(ref: CatalogRef, genre: str | None, skip: int) -> str:
    parts: list[str] = []
    if genre:
        parts.append(f"genre={quote(genre)}")
    if skip:
        parts.append(f"skip={skip}")
    if not parts:
        return catalog_path(ref)
    return f"catalog/{ref.type}/{ref.id}/{'&'.join(parts)}.json"
```

- [ ] **Step 4: Run test, gates, commit**

Run: `uv run pytest tests/infrastructure/addons/test_parsing.py -q` → PASS.
Run gates → pass.

```bash
git add src/gravitas/infrastructure/addons/parsing.py tests/infrastructure/addons/test_parsing.py
git commit -m "feat(catalog): catalog_path_extra encodes genre + skip params"
```

---

### Task 3: Thread `genre`/`skip` through the port, client, and repository

**Files:**
- Modify: `src/gravitas/domain/ports.py`
- Modify: `src/gravitas/infrastructure/addons/client.py`
- Modify: `src/gravitas/application/addon_repository.py`
- Test: `tests/infrastructure/addons/test_client.py`

**Interfaces:**
- Produces: `AddonSource.fetch_catalog(manifest, ref, *, genre: str | None = None, skip: int = 0)`; matching `AddonClient.fetch_catalog`; new `AddonRepository.fetch_catalog_page(manifest, ref, *, genre=None, skip=0) -> list[MediaItem]` (no error swallowing).

- [ ] **Step 1: Write failing test**

Add to `tests/infrastructure/addons/test_client.py` (follow the existing `respx`/`httpx.Response` fixture style already in that file):

```python
@respx.mock
async def test_fetch_catalog_builds_extra_path() -> None:
    respx.get("https://a/catalog/movie/top/genre=Action&skip=100.json").mock(
        return_value=httpx.Response(200, json={"metas": [{"id": "tt1", "type": "movie", "name": "A"}]})
    )
    manifest = AddonManifest(
        id="a", name="A", version="1", resources=("catalog",), types=("movie",),
        catalogs=(CatalogRef(type="movie", id="top", name="T"),), base_url="https://a/",
    )
    async with httpx.AsyncClient() as http:
        client = AddonClient(http)
        items = await client.fetch_catalog(manifest, manifest.catalogs[0], genre="Action", skip=100)
    assert items[0].id == "tt1"
```

(Match the existing imports at the top of the test file — `AddonManifest`, `CatalogRef`, `AddonClient`, `respx`, `httpx` are already imported there for the existing `test_fetch_catalog_builds_path`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/infrastructure/addons/test_client.py::test_fetch_catalog_builds_extra_path -q`
Expected: FAIL — `fetch_catalog()` got an unexpected keyword argument `genre`.

- [ ] **Step 3: Update the port**

In `src/gravitas/domain/ports.py`, change the `AddonSource.fetch_catalog` line to:

```python
    async def fetch_catalog(
        self, manifest: AddonManifest, ref: CatalogRef, *, genre: str | None = None, skip: int = 0
    ) -> list[MediaItem]: ...
```

- [ ] **Step 4: Update the client**

In `src/gravitas/infrastructure/addons/client.py`, replace `fetch_catalog`:

```python
    async def fetch_catalog(
        self, manifest: AddonManifest, ref: CatalogRef, *, genre: str | None = None, skip: int = 0
    ) -> list[MediaItem]:
        path = (
            parsing.catalog_path_extra(ref, genre, skip)
            if (genre or skip)
            else parsing.catalog_path(ref)
        )
        data = await self._get_json(manifest.base_url + path)
        return parsing.parse_catalog(data)
```

- [ ] **Step 5: Add the repository page fetch**

In `src/gravitas/application/addon_repository.py`, add a method (leave `aggregate_catalog` unchanged — Home still uses its fault isolation):

```python
    async def fetch_catalog_page(
        self, manifest: AddonManifest, ref: CatalogRef, *, genre: str | None = None, skip: int = 0
    ) -> list[MediaItem]:
        return await self._source.fetch_catalog(manifest, ref, genre=genre, skip=skip)
```

- [ ] **Step 6: Run tests, gates, commit**

Run: `uv run pytest tests/infrastructure/addons/test_client.py -q` → PASS. Then `uv run pytest -q` → full suite still green.
Run gates → pass.

```bash
git add src/gravitas/domain/ports.py src/gravitas/infrastructure/addons/client.py src/gravitas/application/addon_repository.py tests/infrastructure/addons/test_client.py
git commit -m "feat(catalog): fetch_catalog accepts genre + skip; repo page fetch"
```

---

### Task 4: `BrowseBoard` use case + catalog resolution + filter options

**Files:**
- Modify: `src/gravitas/application/addon_repository.py`
- Create: `src/gravitas/application/browse_board.py`
- Test: `tests/application/test_browse_board.py`

**Interfaces:**
- Consumes: `AddonRepository.fetch_catalog_page` (Task 3); `CatalogRef.genres` (Task 1).
- Produces:
  - `AddonRepository.resolve_catalog(addon_id, type, catalog_id) -> tuple[AddonManifest, CatalogRef] | None`
  - `AddonRepository.catalog_options() -> list[CatalogOption]` with `CatalogOption(addon_id, type, catalog_id, label, genres)`
  - `BrowseBoard` with `PAGE_SIZE = 100`, `async __call__(addon_id, type, catalog_id, *, genre=None, skip=0) -> BoardPage`; `BoardPage(items, has_more)`.

- [ ] **Step 1: Write failing tests**

Create `tests/application/test_browse_board.py`:

```python
from gravitas.application.addon_repository import AddonRepository, CatalogOption
from gravitas.application.browse_board import BoardPage, BrowseBoard
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)


def _item(i: int) -> MediaItem:
    return MediaItem(id=f"tt{i}", type="movie", name=str(i), poster=None)


class FakeSource:
    def __init__(self, page: list[MediaItem]) -> None:
        self.page = page
        self.calls: list[tuple[str, str | None, int]] = []

    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id="a", name="Addon A", version="1", resources=("catalog",), types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top", genres=("Action",)),),
            base_url=url,
        )

    async def fetch_catalog(
        self, manifest: AddonManifest, ref: CatalogRef, *, genre: str | None = None, skip: int = 0
    ) -> list[MediaItem]:
        self.calls.append((ref.id, genre, skip))
        return self.page

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        raise NotImplementedError

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        raise NotImplementedError


async def _installed_repo(page: list[MediaItem]) -> AddonRepository:
    repo = AddonRepository(FakeSource(page))
    await repo.install("https://a/manifest.json")
    return repo


async def test_browse_board_full_page_has_more() -> None:
    repo = await _installed_repo([_item(i) for i in range(100)])
    page = await BrowseBoard(repo)("a", "movie", "top", genre="Action", skip=0)
    assert isinstance(page, BoardPage)
    assert len(page.items) == 100
    assert page.has_more is True


async def test_browse_board_short_page_no_more() -> None:
    repo = await _installed_repo([_item(i) for i in range(10)])
    page = await BrowseBoard(repo)("a", "movie", "top")
    assert page.has_more is False


async def test_browse_board_unknown_catalog_is_empty() -> None:
    repo = await _installed_repo([_item(0)])
    page = await BrowseBoard(repo)("a", "movie", "nope")
    assert page.items == []
    assert page.has_more is False


async def test_resolve_catalog_and_options() -> None:
    repo = await _installed_repo([_item(0)])
    resolved = repo.resolve_catalog("a", "movie", "top")
    assert resolved is not None and resolved[1].id == "top"
    options = repo.catalog_options()
    assert options == [
        CatalogOption(addon_id="a", type="movie", catalog_id="top", label="Top", genres=("Action",))
    ]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/application/test_browse_board.py -q`
Expected: FAIL — `browse_board` module / `CatalogOption` / `resolve_catalog` missing.

- [ ] **Step 3: Add repo resolution + options**

In `src/gravitas/application/addon_repository.py`, add the import and dataclass near the top (after the existing imports), and the two methods on `AddonRepository`:

```python
from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CatalogOption:
    addon_id: str
    type: MediaType
    catalog_id: str
    label: str
    genres: tuple[str, ...]
```

```python
    def resolve_catalog(
        self, addon_id: str, type: MediaType, catalog_id: str
    ) -> tuple[AddonManifest, CatalogRef] | None:
        for manifest in self._manifests:
            if manifest.id != addon_id:
                continue
            for ref in manifest.catalogs:
                if ref.type == type and ref.id == catalog_id:
                    return manifest, ref
        return None

    def catalog_options(self) -> list[CatalogOption]:
        name_counts = Counter(ref.name for m in self._manifests for ref in m.catalogs)
        options: list[CatalogOption] = []
        for manifest in self._manifests:
            for ref in manifest.catalogs:
                label = ref.name if name_counts[ref.name] == 1 else f"{ref.name} ({manifest.name})"
                options.append(
                    CatalogOption(
                        addon_id=manifest.id,
                        type=ref.type,
                        catalog_id=ref.id,
                        label=label,
                        genres=ref.genres,
                    )
                )
        return options
```

- [ ] **Step 4: Create `BrowseBoard`**

Create `src/gravitas/application/browse_board.py`:

```python
"""Use case: fetch one page of a single catalog for the Discover board."""

from __future__ import annotations

from dataclasses import dataclass

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaItem, MediaType


@dataclass(frozen=True, slots=True)
class BoardPage:
    items: list[MediaItem]
    has_more: bool


class BrowseBoard:
    PAGE_SIZE = 100

    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(
        self,
        addon_id: str,
        type: MediaType,
        catalog_id: str,
        *,
        genre: str | None = None,
        skip: int = 0,
    ) -> BoardPage:
        resolved = self._repo.resolve_catalog(addon_id, type, catalog_id)
        if resolved is None:
            return BoardPage(items=[], has_more=False)
        manifest, ref = resolved
        items = await self._repo.fetch_catalog_page(manifest, ref, genre=genre, skip=skip)
        return BoardPage(items=items, has_more=len(items) == self.PAGE_SIZE)
```

- [ ] **Step 5: Run tests, gates, commit**

Run: `uv run pytest tests/application/test_browse_board.py -q` → 4 PASS. Then `uv run pytest -q` → green.
Run gates → pass.

```bash
git add src/gravitas/application/addon_repository.py src/gravitas/application/browse_board.py tests/application/test_browse_board.py
git commit -m "feat(board): BrowseBoard use case + catalog resolution + filter options"
```

---

### Task 5: `addon_id` on `CatalogRow` and the rows model

Carry addon identity to the UI so "See All" can target one addon's catalog.

**Files:**
- Modify: `src/gravitas/application/browse_catalog.py`
- Modify: `src/gravitas/presentation/models/catalog_rows_model.py`
- Test: `tests/application/test_use_cases.py`, `tests/presentation/test_catalog_rows_model.py`

**Interfaces:**
- Produces: `CatalogRow(title, addon_id, type, catalog_id, items)`; `CatalogRowsModel` gains `AddonIdRole` and QML role name `addonId`.

- [ ] **Step 1: Update failing tests**

In `tests/application/test_use_cases.py`, update the expected row in `test_browse_catalog_builds_rows` to include `addon_id`. The `FakeSource` manifest there has `id="fake"`, so:

```python
    assert rows == [
        CatalogRow(
            title="Top",
            addon_id="fake",
            type="movie",
            catalog_id="top",
            items=[MediaItem(id="tt1", type="movie", name="A", poster=None)],
        )
    ]
```

(If the existing `FakeSource` manifest id differs, use that id. Verify by reading the `FakeSource.fetch_manifest` in that test file.)

In `tests/presentation/test_catalog_rows_model.py`, update the `_row` helper and add an addonId assertion:

```python
def _row(title: str, catalog_id: str, name: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type="movie",
        catalog_id=catalog_id,
        items=[MediaItem(id="tt1", type="movie", name=name, poster="http://p/1.jpg")],
    )
```

And in `test_set_rows_exposes_roles`, add:

```python
    assert model.data(index, CatalogRowsModel.AddonIdRole) == "a"
```

And in `test_role_names_are_stringified`, widen the expected set:

```python
    assert {"title", "addonId", "type", "catalogId", "posters"} <= names
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/application/test_use_cases.py::test_browse_catalog_builds_rows tests/presentation/test_catalog_rows_model.py -q`
Expected: FAIL — `CatalogRow` has no `addon_id` / model has no `AddonIdRole`.

- [ ] **Step 3: Add `addon_id` to `CatalogRow` + `BrowseCatalog`**

In `src/gravitas/application/browse_catalog.py`, add the field and fill it:

```python
@dataclass(frozen=True, slots=True)
class CatalogRow:
    title: str
    addon_id: str
    type: MediaType
    catalog_id: str
    items: list[MediaItem]
```

In `BrowseCatalog.__call__`, the append becomes:

```python
            rows.append(
                CatalogRow(
                    title=ref.name,
                    addon_id=manifest.id,
                    type=ref.type,
                    catalog_id=ref.id,
                    items=items,
                )
            )
```

- [ ] **Step 4: Add `AddonIdRole` to `CatalogRowsModel`**

In `src/gravitas/presentation/models/catalog_rows_model.py`:

1. Add the role constant (renumber the following roles):

```python
    TitleRole = Qt.ItemDataRole.UserRole + 1
    AddonIdRole = Qt.ItemDataRole.UserRole + 2
    TypeRole = Qt.ItemDataRole.UserRole + 3
    CatalogIdRole = Qt.ItemDataRole.UserRole + 4
    PostersRole = Qt.ItemDataRole.UserRole + 5
```

2. Widen the internal tuple type and store `addon_id`:

```python
        self._rows: list[tuple[str, str, str, str, PosterGridModel]] = []
```

```python
        for row in rows:
            poster_model = PosterGridModel()
            poster_model.set_items(row.items)
            built.append((row.title, row.addon_id, row.type, row.catalog_id, poster_model))
```

3. Update `data()` unpacking and match arms:

```python
        title, addon_id, type_, catalog_id, posters = self._rows[index.row()]
        match role:
            case CatalogRowsModel.TitleRole:
                return title
            case CatalogRowsModel.AddonIdRole:
                return addon_id
            case CatalogRowsModel.TypeRole:
                return type_
            case CatalogRowsModel.CatalogIdRole:
                return catalog_id
            case CatalogRowsModel.PostersRole:
                return posters
        return None
```

4. Update `roleNames()`:

```python
        return {
            CatalogRowsModel.TitleRole: QByteArray(b"title"),
            CatalogRowsModel.AddonIdRole: QByteArray(b"addonId"),
            CatalogRowsModel.TypeRole: QByteArray(b"type"),
            CatalogRowsModel.CatalogIdRole: QByteArray(b"catalogId"),
            CatalogRowsModel.PostersRole: QByteArray(b"posters"),
        }
```

- [ ] **Step 5: Run tests, gates, commit**

Run: `uv run pytest tests/application/test_use_cases.py tests/presentation/test_catalog_rows_model.py -q` → PASS. Then `uv run pytest -q` → green.
Run gates → pass.

```bash
git add src/gravitas/application/browse_catalog.py src/gravitas/presentation/models/catalog_rows_model.py tests/application/test_use_cases.py tests/presentation/test_catalog_rows_model.py
git commit -m "feat(board): carry addon_id on CatalogRow + rows model"
```

---

### Task 6: `PosterGridModel.append_items` for infinite scroll

**Files:**
- Modify: `src/gravitas/presentation/models/poster_grid_model.py`
- Test: `tests/presentation/test_models.py`

**Interfaces:**
- Produces: `PosterGridModel.append_items(items: list[MediaItem]) -> None`.

- [ ] **Step 1: Write failing test**

Add to `tests/presentation/test_models.py`:

```python
def test_poster_model_append_items(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([MediaItem(id="tt1", type="movie", name="A", poster=None)])
    model.append_items([MediaItem(id="tt2", type="movie", name="B", poster=None)])
    assert model.rowCount() == 2
    assert model.item_at(1).id == "tt2"
    model.append_items([])  # no-op
    assert model.rowCount() == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/presentation/test_models.py::test_poster_model_append_items -q`
Expected: FAIL — `append_items` not defined.

- [ ] **Step 3: Implement**

In `src/gravitas/presentation/models/poster_grid_model.py`, add after `set_items` (the module already defines `_ROOT_INDEX`):

```python
    def append_items(self, items: list[MediaItem]) -> None:
        if not items:
            return
        start = len(self._items)
        self.beginInsertRows(_ROOT_INDEX, start, start + len(items) - 1)
        self._items.extend(items)
        self.endInsertRows()
```

- [ ] **Step 4: Run test, gates, commit**

Run: `uv run pytest tests/presentation/test_models.py -q` → PASS.
Run gates → pass.

```bash
git add src/gravitas/presentation/models/poster_grid_model.py tests/presentation/test_models.py
git commit -m "feat(ui): PosterGridModel.append_items for infinite scroll"
```

---

### Task 7: `DiscoverController`

**Files:**
- Create: `src/gravitas/presentation/controllers/discover_controller.py`
- Test: `tests/presentation/test_discover_controller.py`

**Interfaces:**
- Consumes: `BrowseBoard` / `BoardPage` (Task 4), `AddonRepository.catalog_options` + `CatalogOption` (Task 4), `PosterGridModel` with `set_items`/`append_items` (Task 6).
- Produces: `DiscoverController(browse, repo, model)` with `@qasync.asyncSlot` slots `open(addon_id, type, catalog_id)`, `selectType(index)`, `selectCatalog(index)`, `selectGenre(index)`, `loadMore()`; notifying properties `typeOptions`, `catalogOptions`, `genreOptions` (QVariantList), `typeIndex`, `catalogIndex`, `genreIndex` (int); signals `errorOccurred(str)`, `loadingChanged(bool)`, `optionsChanged()`.

- [ ] **Step 1: Write failing tests**

Create `tests/presentation/test_discover_controller.py`:

```python
from gravitas.application.addon_repository import AddonRepository, CatalogOption
from gravitas.application.browse_board import BoardPage
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import MediaItem
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.models.poster_grid_model import PosterGridModel


def _items(n: int, offset: int = 0) -> list[MediaItem]:
    return [MediaItem(id=f"tt{offset + i}", type="movie", name=str(offset + i), poster=None) for i in range(n)]


class FakeRepo:
    def catalog_options(self) -> list[CatalogOption]:
        return [
            CatalogOption(addon_id="a", type="movie", catalog_id="top", label="Top", genres=("Action",)),
            CatalogOption(addon_id="a", type="series", catalog_id="pop", label="Pop", genres=()),
        ]


class FakeBrowse:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str | None, int]] = []
        self.has_more = True

    async def __call__(self, addon_id, type, catalog_id, *, genre=None, skip=0):  # noqa: ANN001
        self.calls.append((addon_id, type, catalog_id, genre, skip))
        return BoardPage(items=_items(100, offset=skip), has_more=self.has_more)


async def test_open_loads_first_page_and_options(qapp: object) -> None:
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    assert model.rowCount() == 100
    assert list(ctl.typeOptions) == ["movie", "series"]
    assert list(ctl.catalogOptions) == ["Top"]
    assert list(ctl.genreOptions) == ["All", "Action"]
    assert browse.calls[-1] == ("a", "movie", "top", None, 0)


async def test_select_genre_reloads_with_genre(qapp: object) -> None:
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    await ctl.selectGenre(1)  # "Action"
    assert browse.calls[-1] == ("a", "movie", "top", "Action", 0)
    assert model.rowCount() == 100  # reset, not appended


async def test_load_more_appends_and_respects_has_more(qapp: object) -> None:
    model = PosterGridModel()
    browse = FakeBrowse()
    ctl = DiscoverController(browse, FakeRepo(), model)  # type: ignore[arg-type]
    await ctl.open("a", "movie", "top")
    await ctl.loadMore()
    assert browse.calls[-1] == ("a", "movie", "top", None, 100)
    assert model.rowCount() == 200
    browse.has_more = False
    await ctl.loadMore()  # fetches skip=200, has_more now False
    assert model.rowCount() == 300
    calls_before = len(browse.calls)
    await ctl.loadMore()  # no has_more -> no fetch
    assert len(browse.calls) == calls_before


async def test_error_emits_and_clears_loading(qapp: object) -> None:
    class Boom:
        async def __call__(self, *a, **k):  # noqa: ANN002, ANN003
            raise AddonUnreachable("boom")

    model = PosterGridModel()
    ctl = DiscoverController(Boom(), FakeRepo(), model)  # type: ignore[arg-type]
    errors: list[str] = []
    loading: list[bool] = []
    ctl.errorOccurred.connect(errors.append)
    ctl.loadingChanged.connect(loading.append)
    await ctl.open("a", "movie", "top")
    assert errors == ["boom"]
    assert loading == [True, False]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/presentation/test_discover_controller.py -q`
Expected: FAIL — `discover_controller` module missing.

- [ ] **Step 3: Implement**

Create `src/gravitas/presentation/controllers/discover_controller.py`:

```python
"""QObject bridge: drive the Discover board (filters + paginated grid)."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.addon_repository import AddonRepository, CatalogOption
from gravitas.application.browse_board import BrowseBoard
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import MediaType


class DiscoverController(QObject):
    errorOccurred = Signal(str)
    loadingChanged = Signal(bool)
    optionsChanged = Signal()

    def __init__(
        self, browse: BrowseBoard, repo: AddonRepository, model: object
    ) -> None:
        super().__init__()
        self._browse = browse
        self._repo = repo
        self._model = model
        self._options: list[CatalogOption] = []
        self._type: MediaType = "movie"
        self._catalog_idx = 0
        self._genre: str | None = None
        self._skip = 0
        self._has_more = False
        self._loading = False

    def _types(self) -> list[str]:
        seen: list[str] = []
        for opt in self._options:
            if opt.type not in seen:
                seen.append(opt.type)
        return seen

    def _catalogs_for_type(self) -> list[CatalogOption]:
        return [opt for opt in self._options if opt.type == self._type]

    @Property("QVariantList", notify=optionsChanged)  # type: ignore[misc]
    def typeOptions(self) -> list[str]:
        return self._types()

    @Property("QVariantList", notify=optionsChanged)  # type: ignore[misc]
    def catalogOptions(self) -> list[str]:
        return [opt.label for opt in self._catalogs_for_type()]

    @Property("QVariantList", notify=optionsChanged)  # type: ignore[misc]
    def genreOptions(self) -> list[str]:
        cats = self._catalogs_for_type()
        if not cats or self._catalog_idx >= len(cats):
            return ["All"]
        return ["All", *cats[self._catalog_idx].genres]

    @Property(int, notify=optionsChanged)  # type: ignore[misc]
    def typeIndex(self) -> int:
        types = self._types()
        return types.index(self._type) if self._type in types else 0

    @Property(int, notify=optionsChanged)  # type: ignore[misc]
    def catalogIndex(self) -> int:
        return self._catalog_idx

    @Property(int, notify=optionsChanged)  # type: ignore[misc]
    def genreIndex(self) -> int:
        if self._genre is None:
            return 0
        genres = self.genreOptions
        return genres.index(self._genre) if self._genre in genres else 0

    @asyncSlot(str, str, str)  # type: ignore[untyped-decorator]
    async def open(self, addon_id: str, type: str, catalog_id: str) -> None:
        self._options = self._repo.catalog_options()
        self._type = "series" if type == "series" else "movie"
        cats = self._catalogs_for_type()
        self._catalog_idx = next(
            (i for i, opt in enumerate(cats) if opt.addon_id == addon_id and opt.catalog_id == catalog_id),
            0,
        )
        self._genre = None
        self.optionsChanged.emit()
        await self._reload()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def selectType(self, index: int) -> None:
        types = self._types()
        if not 0 <= index < len(types):
            return
        self._type = "series" if types[index] == "series" else "movie"
        self._catalog_idx = 0
        self._genre = None
        self.optionsChanged.emit()
        await self._reload()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def selectCatalog(self, index: int) -> None:
        if not 0 <= index < len(self._catalogs_for_type()):
            return
        self._catalog_idx = index
        self._genre = None
        self.optionsChanged.emit()
        await self._reload()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def selectGenre(self, index: int) -> None:
        genres = self.genreOptions
        if not 0 <= index < len(genres):
            return
        self._genre = None if index == 0 else genres[index]
        await self._reload()

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def loadMore(self) -> None:
        if self._loading or not self._has_more:
            return
        cats = self._catalogs_for_type()
        if not cats:
            return
        self._skip += BrowseBoard.PAGE_SIZE
        await self._fetch(cats[self._catalog_idx], append=True)

    async def _reload(self) -> None:
        self._skip = 0
        cats = self._catalogs_for_type()
        if not cats:
            self._model.set_items([])  # type: ignore[attr-defined]
            self._has_more = False
            return
        await self._fetch(cats[self._catalog_idx], append=False)

    async def _fetch(self, cat: CatalogOption, *, append: bool) -> None:
        self._loading = True
        self.loadingChanged.emit(True)
        try:
            page = await self._browse(
                cat.addon_id, cat.type, cat.catalog_id, genre=self._genre, skip=self._skip
            )
            if append:
                self._model.append_items(page.items)  # type: ignore[attr-defined]
            else:
                self._model.set_items(page.items)  # type: ignore[attr-defined]
            self._has_more = page.has_more
        except GravitasError as exc:
            self._has_more = False
            self.errorOccurred.emit(str(exc))
        finally:
            self._loading = False
            self.loadingChanged.emit(False)
```

Note: `model` is typed `object` with `# type: ignore[attr-defined]` on `set_items`/`append_items` calls to avoid a hard import-time coupling in the annotation; the real object is a `PosterGridModel`. If mypy prefers, import `PosterGridModel` and type the parameter as `PosterGridModel` and drop those ignores — do whichever keeps `mypy src` clean.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/presentation/test_discover_controller.py -q`
Expected: 4 PASS.

- [ ] **Step 5: Gates + commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: pass. If mypy reports a different ignore code for `@Property`/`@asyncSlot` than written, replace with the reported code.

```bash
git add src/gravitas/presentation/controllers/discover_controller.py tests/presentation/test_discover_controller.py
git commit -m "feat(board): DiscoverController with filters + infinite scroll"
```

---

### Task 8: Wire `DiscoverController` into the composition root

**Files:**
- Modify: `src/gravitas/main.py`
- Test: `tests/test_composition.py`

**Interfaces:**
- Consumes: `DiscoverController` (Task 7), `BrowseBoard` (Task 4), `PosterGridModel` (Task 6).
- Produces: context properties `discoverController` and `discoverModel`.

- [ ] **Step 1: Update the failing composition test**

In `tests/test_composition.py`, add two assertions alongside the existing context-property checks:

```python
        assert ctx.contextProperty("discoverController") is not None
        assert ctx.contextProperty("discoverModel") is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_composition.py -q`
Expected: FAIL — `discoverController` is `None`.

- [ ] **Step 3: Wire it in `main.py`**

In `src/gravitas/main.py`:

1. Add imports:

```python
from gravitas.application.browse_board import BrowseBoard
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.models.poster_grid_model import PosterGridModel
```

2. Construct the discover model + controller (near the other models/controllers, after `rows_model`):

```python
    discover_model = PosterGridModel()
    discover_controller = DiscoverController(BrowseBoard(repo), repo, discover_model)
```

3. Register context properties (next to the others):

```python
    ctx.setContextProperty("discoverController", discover_controller)
    ctx.setContextProperty("discoverModel", discover_model)
```

4. Add both to the `engine._gravitas_refs` keep-alive tuple:

```python
    engine._gravitas_refs = (  # type: ignore[attr-defined]
        catalog_controller,
        detail_controller,
        player_controller,
        addon_controller,
        discover_controller,
        rows_model,
        discover_model,
        stream_model,
    )
```

- [ ] **Step 4: Run test, gates, commit**

Run: `uv run pytest tests/test_composition.py -q` → PASS. Then `uv run pytest -q` → green.
Run gates → pass.

```bash
git add src/gravitas/main.py tests/test_composition.py
git commit -m "feat(board): wire DiscoverController into composition root"
```

---

### Task 9: Discover board QML + `See All` navigation

**Files:**
- Create: `src/gravitas/presentation/qml/Discover.qml`
- Modify: `src/gravitas/presentation/qml/components/CatalogRowStrip.qml`
- Modify: `src/gravitas/presentation/qml/Home.qml`
- Modify: `src/gravitas/presentation/qml/Main.qml`
- Test: `tests/test_composition.py` (the existing `assert engine.rootObjects()` now also loads `Discover.qml` when it's pushed — but it is not pushed at load; add a focused load check, see Step 5)

**Interfaces:**
- Consumes: context properties `discoverController` (slots `open`/`selectType`/`selectCatalog`/`selectGenre`/`loadMore`; properties `typeOptions`/`catalogOptions`/`genreOptions`/`typeIndex`/`catalogIndex`/`genreIndex`; signals `loadingChanged`/`errorOccurred`) and `discoverModel`; `CatalogRowsModel` role `addonId`.
- Produces: `seeAll(string addonId, string type, string catalogId)` through `CatalogRowStrip` → `Home` → `Main`.

- [ ] **Step 1: Propagate `addonId` in `CatalogRowStrip.qml`**

In `src/gravitas/presentation/qml/components/CatalogRowStrip.qml`, add an `addonId` property and widen the `seeAll` signal + emit. Change the property block and the See All click:

```qml
    property string title
    property string addonId
    property string type
    property string catalogId
    property var posters
    signal openDetail(string type, string id)
    signal seeAll(string addonId, string type, string catalogId)
```

```qml
                onClicked: root.seeAll(root.addonId, root.type, root.catalogId)
```

- [ ] **Step 2: Propagate in `Home.qml`**

In `src/gravitas/presentation/qml/Home.qml`, widen the `seeAll` signal and pass `addonId` into the delegate:

```qml
    signal seeAll(string addonId, string type, string catalogId)
```

In the `CatalogRowStrip` delegate:

```qml
        delegate: CatalogRowStrip {
            width: rowsView.width
            title: model.title
            addonId: model.addonId
            type: model.type
            catalogId: model.catalogId
            posters: model.posters
            onOpenDetail: (t, id) => home.openDetail(t, id)
            onSeeAll: (aid, t, cid) => home.seeAll(aid, t, cid)
        }
```

- [ ] **Step 3: Create `Discover.qml`**

Create `src/gravitas/presentation/qml/Discover.qml`:

```qml
import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: root
    signal openDetail(string type, string id)
    signal back()

    Row {
        id: filters
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 12
        height: 44
        spacing: 8

        Button { text: "‹ Back"; onClicked: root.back() }

        ComboBox {
            id: typeBox
            width: 160
            model: discoverController.typeOptions
            currentIndex: discoverController.typeIndex
            onActivated: (index) => discoverController.selectType(index)
        }
        ComboBox {
            id: catalogBox
            width: 220
            model: discoverController.catalogOptions
            currentIndex: discoverController.catalogIndex
            onActivated: (index) => discoverController.selectCatalog(index)
        }
        ComboBox {
            id: genreBox
            width: 200
            model: discoverController.genreOptions
            currentIndex: discoverController.genreIndex
            onActivated: (index) => discoverController.selectGenre(index)
        }
    }

    GridView {
        id: grid
        anchors.top: filters.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        cellWidth: 180
        cellHeight: 300
        clip: true
        model: discoverModel
        delegate: PosterCard {
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            onClicked: root.openDetail(model.type, model.id)
        }
        onAtYEndChanged: if (atYEnd) discoverController.loadMore()
    }

    BusyIndicator {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: discoverController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
}
```

- [ ] **Step 4: Push the board from `Main.qml`**

In `src/gravitas/presentation/qml/Main.qml`:

1. Change the `Home` handler to open the board (replace the `onSeeAll` no-op):

```qml
    Component {
        id: homePage
        Home {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onSeeAll: (addonId, type, catalogId) => {
                discoverController.open(addonId, type, catalogId)
                stack.push(discoverPage)
            }
        }
    }
```

2. Add the `discoverPage` component (next to `detailPage`):

```qml
    Component {
        id: discoverPage
        Discover {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onBack: () => stack.pop()
        }
    }
```

3. Add an error-bar connection for the discover controller (next to the other `Connections`):

```qml
    Connections {
        target: discoverController
        function onErrorOccurred(msg) { errorBar.show(msg) }
    }
```

- [ ] **Step 5: Add a headless Discover.qml load check**

The composition test loads `Main.qml`, but `Discover.qml` is only instantiated when pushed — a parse error in it would not surface at load. Add a focused test that instantiates `Discover.qml` under the offscreen platform. Append to `tests/test_composition.py`:

```python
def test_discover_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()

    class _StubModel(QObject):
        pass

    stub = _StubModel()
    engine.rootContext().setContextProperty("discoverController", stub)
    engine.rootContext().setContextProperty("discoverModel", stub)
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Discover.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Discover.qml failed to load: {component.errorString()}"
```

(This catches QML syntax/type errors in `Discover.qml` headlessly. Binding
values against the stub may log runtime warnings but the component still
instantiates; the assertion only requires a successful parse/create.)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, including `test_discover_qml_loads` and the existing `assert engine.rootObjects()` (which still loads `Main.qml → Home → CatalogRowStrip` with the widened `seeAll`).

- [ ] **Step 7: Gates**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: pass.

- [ ] **Step 8: Launch and eyeball**

Run: `uv run gravitas`
Expected (needs a display): Home shows the category rows. Clicking "See All" on a row opens the Discover board — three dropdowns (Type / Catalog / Genre) pre-selected to that catalog, a poster grid below. Changing Genre reloads the grid; scrolling to the bottom loads more posters (Cinemeta's "Popular" supports genre + skip). Back returns Home. Clicking a poster opens Detail. Close the app.

- [ ] **Step 9: Commit**

```bash
git add src/gravitas/presentation/qml/Discover.qml \
        src/gravitas/presentation/qml/components/CatalogRowStrip.qml \
        src/gravitas/presentation/qml/Home.qml \
        src/gravitas/presentation/qml/Main.qml \
        tests/test_composition.py
git commit -m "feat(board): Discover board QML + See All navigation"
```

---

## Self-Review

**Spec coverage:**
- `CatalogRef` genres/supports_skip + manifest parsing (modern + legacy) → Task 1. ✓
- `catalog_path_extra` encoding → Task 2. ✓
- `fetch_catalog` genre/skip through port/client/repo → Task 3. ✓
- `BrowseBoard` + `BoardPage` + `has_more` heuristic + `resolve_catalog` + `catalog_options`/`CatalogOption` → Task 4. ✓
- `addon_id` on `CatalogRow` + rows model role → Task 5. ✓
- `PosterGridModel.append_items` → Task 6. ✓
- `DiscoverController` (open/setType/setCatalog/setGenre/loadMore, option properties, error/loading) → Task 7. ✓
- Composition wiring (`discoverController`/`discoverModel`) → Task 8. ✓
- `Discover.qml` (3 dropdowns, grid, infinite scroll via `atYEnd`, back), `See All` addonId propagation, `Main.qml` push → Task 9. ✓
- Navigation (See All → pushed board, Back → Home, poster → Detail) → Task 9. ✓
- Genre "All" == no param; page size 100; single-catalog error propagation → Tasks 4/7. ✓
- Out of scope (sidebar, nav rail, cross-addon board, search, filter persistence) — no task introduces them. ✓

**Placeholder scan:** No TBD/TODO in executable steps. Every code step shows complete code. Two "use whichever keeps mypy clean" notes (Property/asyncSlot ignore codes; controller `model` annotation) are explicit fallbacks for untyped-third-party variance, not missing content — the primary code is written out.

**Type consistency:** `CatalogRow(title, addon_id, type, catalog_id, items)` consistent Tasks 5/9 (QML reads `model.addonId`). `CatalogOption(addon_id, type, catalog_id, label, genres)` consistent Tasks 4/7. `BrowseBoard.__call__(addon_id, type, catalog_id, *, genre, skip)` and `PAGE_SIZE` consistent Tasks 4/7. `BoardPage(items, has_more)` consistent Tasks 4/7. `fetch_catalog(..., *, genre=None, skip=0)` consistent Tasks 3 (port/client) and used by Task 4 repo. Controller slot names (`open`/`selectType`/`selectCatalog`/`selectGenre`/`loadMore`) and property names (`typeOptions`/`catalogOptions`/`genreOptions`/`typeIndex`/`catalogIndex`/`genreIndex`) consistent Tasks 7/9. `seeAll(addonId, type, catalogId)` consistent across `CatalogRowStrip`/`Home`/`Main` (Task 9). Role numbering in `CatalogRowsModel` renumbered coherently (Task 5).
