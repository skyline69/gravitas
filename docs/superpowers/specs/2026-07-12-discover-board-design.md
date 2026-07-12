# Gravitas — Discover Board (Step 2)

**Date:** 2026-07-12
**Status:** Approved (brainstorming)

## Context

Step 1 shipped Stremio-style Home category rows. Each row's "See All" button
emits `seeAll(type, catalogId)`, which `Main.qml` currently ignores. This step
makes "See All" open a **Discover board**: a full, filterable, infinitely
scrolling grid for a catalog — the image-4 page (type / catalog / genre
dropdowns + grid), minus the detail sidebar.

This is **step 2 of 3**:

1. Home category rows — shipped.
2. **Discover board** — this spec.
3. Detail sidebar enrichment (`year`/`runtime`/`rating`/`genres`/`cast` on
   `MetaDetail` + sidebar UI) — its own spec, out of scope here.

## Decisions

| Topic | Decision |
|-------|----------|
| Reachability | `See All` pushes a `Discover.qml` page onto the StackView. Back returns Home. No nav rail (app chrome stays out of scope). |
| Filters | Three dropdowns: Type, Catalog, Genre. |
| Pagination | Infinite scroll — auto-fetch next page (`skip += pageSize`) near the grid bottom, append to the grid. |
| Addon identity | `See All` carries `addonId`; `CatalogRow` and the `seeAll` signal gain `addonId` (two addons may share a catalog id). |
| Catalog dropdown | Lists `(addon, catalog)` pairs for the selected type across all installed addons. Label = catalog name, disambiguated with the addon name only on collision. |
| Genre dropdown | The selected catalog's genres plus a leading "All" entry (fetch with no `genre` param). Hidden/empty when the catalog exposes no genres. |
| `has_more` | Heuristic: the fetched page returned a full `pageSize` of items. |
| Page size | 100 (Stremio's `skip` step). |
| Poster click | Opens the existing Detail page. |
| Detail sidebar | Out of scope (step 3). |

## Infra / data layer

### `CatalogRef` gains extra metadata

```python
@dataclass(frozen=True, slots=True)
class CatalogRef:
    type: MediaType
    id: str
    name: str
    genres: tuple[str, ...] = ()
    supports_skip: bool = False
```

`parse_manifest` populates them from the catalog's `extra`. Two manifest
shapes are supported:

- Modern: `"extra": [{"name": "genre", "options": [...], "isRequired": bool},
  {"name": "skip"}, {"name": "search"}]` → `genres` from the `genre` entry's
  `options`; `supports_skip = True` if a `skip` entry exists.
- Legacy: `"extraSupported": ["genre", "skip"]` + top-level `"genres": [...]`
  → `genres` from `genres` when `"genre"` is in `extraSupported`;
  `supports_skip = "skip" in extraSupported`.

Absent both → `genres=()`, `supports_skip=False`.

### Catalog path with extras

New pure function in `parsing.py`:

```python
def catalog_path_extra(ref: CatalogRef, genre: str | None, skip: int) -> str:
    parts: list[str] = []
    if genre:
        parts.append(f"genre={genre}")
    if skip:
        parts.append(f"skip={skip}")
    if not parts:
        return f"catalog/{ref.type}/{ref.id}.json"
    return f"catalog/{ref.type}/{ref.id}/{'&'.join(parts)}.json"
```

The genre value is URL-encoded (spaces etc.) before joining. The existing
`catalog_path(ref)` stays for the no-extras case / Home rows.

### Port + adapter + repository

`fetch_catalog` grows optional paging params, defaulting so existing callers
(Home rows) are unaffected:

```python
async def fetch_catalog(
    self, manifest: AddonManifest, ref: CatalogRef,
    *, genre: str | None = None, skip: int = 0,
) -> list[MediaItem]: ...
```

Threaded through `AddonSource` (port), `AddonClient` (uses
`catalog_path_extra` when `genre or skip` else `catalog_path`), and
`AddonRepository.aggregate_catalog` / a new repo method (see below).

## Application layer

### `BrowseBoard` use case

```python
@dataclass(frozen=True, slots=True)
class BoardPage:
    items: list[MediaItem]
    has_more: bool

class BrowseBoard:
    PAGE_SIZE = 100
    def __init__(self, repo: AddonRepository) -> None: ...
    async def __call__(
        self, addon_id: str, type: MediaType, catalog_id: str,
        *, genre: str | None = None, skip: int = 0,
    ) -> BoardPage:
        # resolve (manifest, ref) by addon_id + type + catalog_id,
        # fetch one page, has_more = len(items) == PAGE_SIZE
```

`BrowseBoard` uses the repository to resolve the specific addon's catalog and
fetch a single page (no cross-addon aggregation — the board is one catalog).

### Filter options

A query the controller uses to populate the dropdowns. Lives in
`AddonRepository` (it owns the installed-addon store):

```python
@dataclass(frozen=True, slots=True)
class CatalogOption:
    addon_id: str
    type: MediaType
    catalog_id: str
    label: str          # catalog name, + " (addon name)" only on collision
    genres: tuple[str, ...]

def catalog_options(self) -> list[CatalogOption]: ...
```

The controller derives the Type list (distinct `type`s) and, for the selected
type, the Catalog list, and per selected catalog the Genre list, from this.

## Presentation layer

### `PosterGridModel.append_items`

Add alongside `set_items` for infinite scroll:

```python
def append_items(self, items: list[MediaItem]) -> None:
    if not items:
        return
    start = len(self._items)
    self.beginInsertRows(_ROOT_INDEX, start, start + len(items) - 1)
    self._items.extend(items)
    self.endInsertRows()
```

### `DiscoverController(QObject)`

State: current `addon_id`, `type`, `catalog_id`, `genre` (`None` == All),
`skip`, and a `has_more` flag. Owns a `PosterGridModel` (the grid).

- `open(addon_id, type, catalog_id)` — set state, reset genre=All/skip=0,
  populate dropdown option properties, load first page (`set_items`).
- `setType(type)` — pick that type's first catalog, reset, reload.
- `setCatalog(addon_id, catalog_id)` — reset genre/skip, reload.
- `setGenre(genre)` — `None` for "All"; reset skip, reload.
- `loadMore()` — if `has_more` and not already loading, `skip += PAGE_SIZE`,
  fetch, `append_items`.
- Signals: `errorOccurred(str)`, `loadingChanged(bool)`, and option-list
  change signals so QML dropdowns refresh (`optionsChanged`).
- Exposes the dropdown data to QML as list properties (types, catalog
  labels + ids, genres). A small `QAbstractListModel` or a plain
  `list[str]`/`list[dict]` property works; use string/variant-list properties
  to keep it simple (no new model class unless a role set is needed).

Async slots use `@qasync.asyncSlot`.

### `Discover.qml`

- Top bar: three `ComboBox`es (Type, Catalog, Genre) bound to the controller's
  option properties; `onActivated` → `setType`/`setCatalog`/`setGenre`.
- A wrapping `GridView` (reusing `PosterCard`) bound to the controller's grid
  model; when `atYEnd` (or within a threshold of the end) → `loadMore()`.
- Back button → `stack.pop()`.
- Poster click → `openDetail(type, id)` propagated to `Main.qml`.
- A `BusyIndicator` bound to `loadingChanged`.

### `Main.qml`

- Add a `discoverPage` `Component` wrapping `Discover`.
- `Home.onSeeAll: (addonId, type, catalogId) => { discoverController.open(addonId, type, catalogId); stack.push(discoverPage) }`.
- `Discover.onOpenDetail` → push the existing `detailPage`.

### `Home.qml` / `CatalogRowStrip.qml`

Propagate `addonId` through `seeAll(addonId, type, catalogId)`. `CatalogRow`
already carries `type`/`catalog_id`; add `addon_id`, filled by `BrowseCatalog`
from the owning manifest.

### Composition root (`main.py`)

Construct `DiscoverController` (with `BrowseBoard(repo)` and its
`PosterGridModel`), register it as the `discoverController` context property,
and add it + its model to the `engine._gravitas_refs` keep-alive tuple.

## Error handling

Unchanged model: typed domain errors propagate; `DiscoverController` catches
`GravitasError` and emits `errorOccurred` → the existing error bar. A failed
page fetch during infinite scroll clears the loading flag and stops paging
(does not crash the grid). One unreachable addon does not affect the board of
a working one (the board is single-catalog).

## Testing

- **Parsing:** `parse_manifest` extracts `genres`/`supports_skip` from both
  modern `extra` and legacy `extraSupported`/`genres`; absent → defaults.
- **Path:** `catalog_path_extra` for genre-only, skip-only, both, neither
  (falls back to plain path); genre URL-encoding.
- **`AddonClient.fetch_catalog`:** builds the extra path when `genre`/`skip`
  given, plain path otherwise (fixture-based).
- **`BrowseBoard`:** fake source; `has_more` true on a full page, false on a
  short page; resolves the right `(addon, catalog)`.
- **`catalog_options`:** collision disambiguation; genres carried through.
- **`DiscoverController`:** fake use case — `open` loads first page and sets
  options; `setGenre`/`setCatalog`/`setType` reset skip and reload;
  `loadMore` appends and respects `has_more`; error path emits and clears
  loading.
- **`PosterGridModel.append_items`:** appends via `beginInsertRows`; empty
  input is a no-op.
- **QML:** verified at launch; the composition test's `assert
  engine.rootObjects()` covers loading `Discover.qml` headlessly.
- **Gates:** `ruff check`, `ruff format --check`, `mypy --strict src`,
  `pytest` all green on every commit.

## Out of scope

- Detail sidebar metadata enrichment (step 3).
- Nav rail / search bar / any app-chrome redesign.
- Cross-addon aggregation within the board (the board is one catalog).
- Search extra (`search=` catalog param) — separate future milestone.
- Persisting the last-used filters.
