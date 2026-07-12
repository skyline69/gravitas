# Gravitas — Home Category Rows (Step 1)

**Date:** 2026-07-12
**Status:** Approved (brainstorming)

## Context

The Home page currently flattens every installed addon's catalogs into one flat
`GridView`. The application layer already builds per-catalog rows
(`BrowseCatalog` returns `list[CatalogRow]`), but the presentation layer
discards that structure by concatenating all items into a single
`PosterGridModel`.

This step restores the row structure in the UI: a Stremio-style vertical list of
horizontal poster strips, each with a section title and a "See All" button.

This is **step 1 of 3** in the larger "category rows like Stremio" effort:

1. **Home category rows** — this spec.
2. **Discover board** — the filter-dropdown grid page ("See All" destination).
   Needs catalog `extra` parsing (genre options, pagination) and genre/`skip`
   fetch params. Its own spec.
3. **Detail sidebar enrichment** — add `year`/`runtime`/`rating`/`genres`/`cast`
   to `MetaDetail` + parser + sidebar UI. Its own spec.

Steps 2 and 3 are out of scope here.

## Decisions

| Topic | Decision |
|-------|----------|
| Row structure | Vertical `ListView` of rows; each row = title + See All + horizontal `ListView` of posters |
| Rows model | New `CatalogRowsModel(QAbstractListModel)`; one element per catalog row |
| Poster reuse | Each row owns a per-row `PosterGridModel` instance (existing model, unchanged) + existing `PosterCard.qml` |
| See All (now) | Renders; emits `seeAll(type, catalogId)`; `Main.qml` ignores it until step 2 |
| Addon URL bar | Unchanged — stays on top of Home |
| New network calls | None — same data `BrowseCatalog` already fetches |

## Data layer

Extend `CatalogRow` to carry catalog identity so "See All" can target a specific
catalog in step 2:

```python
@dataclass(frozen=True, slots=True)
class CatalogRow:
    title: str
    type: MediaType
    catalog_id: str
    items: list[MediaItem]
```

`BrowseCatalog.__call__` already iterates `(manifest, ref)` from
`repo.catalog_refs()`. It fills the new fields from `ref`:

```python
rows.append(CatalogRow(title=ref.name, type=ref.type, catalog_id=ref.id, items=items))
```

No new fetches; `aggregate_catalog` is unchanged.

## Presentation layer

### `CatalogRowsModel(QAbstractListModel)`

New model in `presentation/models/`. One element per `CatalogRow`.

Roles:

- `TitleRole` → `title` (str)
- `TypeRole` → `type` (str)
- `CatalogIdRole` → `catalogId` (str)
- `PostersRole` → the row's `PosterGridModel` instance (QObject)

`set_rows(rows: list[CatalogRow])`:

- `beginResetModel()`
- For each row, construct a `PosterGridModel`, call `set_items(row.items)`.
- Store `(title, type, catalog_id, poster_model)` tuples in an internal list —
  this holds a Python reference to each inner model, keeping it alive for QML.
- `endResetModel()`

Returning a `QAbstractListModel` as a role value and binding it as an inner
`ListView`'s `model` in the delegate is a standard PySide6/QML pattern.

### `CatalogController` change

- Constructor takes a `CatalogRowsModel` instead of a `PosterGridModel`.
- `load_catalog` calls `rows = await self._browse()` then
  `self._model.set_rows(rows)` (drops the flattening comprehension).
- `errorOccurred` / `loadingChanged` signals and the `@asyncSlot refresh`
  are unchanged.

### QML

**`Home.qml`** — replace the flat `GridView` with a vertical `ListView`:

- `model: catalogRowsModel`
- `delegate: CatalogRowStrip { ... }`, binding `title`, `type`, `catalogId`,
  `posters` from row roles.
- Propagate two signals up: `openDetail(type, id)` (existing) and a new
  `seeAll(type, catalogId)`.
- Keep the addon URL bar and the `BusyIndicator` exactly as they are.

**`CatalogRowStrip.qml`** (new, in `qml/components/`):

- Properties: `title`, `type`, `catalogId`, `posters` (the inner model).
- Signals: `openDetail(type, id)`, `seeAll(type, catalogId)`.
- Layout: a header row (title `Text` on the left, "See All" `Button`/clickable
  `Text` on the right) above a horizontal `ListView`
  (`orientation: ListView.Horizontal`) whose `delegate` is the existing
  `PosterCard`.
- `PosterCard.onClicked` → `openDetail(model.type, model.id)`.
- See All click → `seeAll(type, catalogId)`.
- Horizontal mouse-wheel scrolling: add a `WheelHandler` on the inner
  `ListView` if the default flick behavior does not scroll on wheel.

**`Main.qml`** — the `Home` component instance gains an `onSeeAll` handler that
is a no-op for now (a comment marking it as the step-2 hook). `onOpenDetail`
is unchanged.

**`PosterCard.qml`** — unchanged.

### Composition root (`main.py`)

- Construct `CatalogRowsModel` instead of the Home `PosterGridModel`
  (the `StreamListModel` / detail wiring is untouched).
- Pass it to `CatalogController`.
- `ctx.setContextProperty("catalogRowsModel", rows_model)` — replacing the
  `posterModel` property (Home no longer references `posterModel`; the detail
  page does not use it).
- Update the `engine._gravitas_refs` keep-alive tuple to hold `rows_model`.

## Error handling

Unchanged from the current design. `BrowseCatalog` propagates typed domain
errors; `CatalogController` catches `GravitasError` and emits `errorOccurred`;
`Main.qml`'s error bar shows it. Per-addon fault isolation stays in
`aggregate_catalog`. An empty repo yields an empty rows list → an empty
`ListView` (no error).

## Testing

- **`CatalogRowsModel`** (new unit test): `set_rows` populates row count; each
  row's `TitleRole` / `TypeRole` / `CatalogIdRole` return expected values; the
  `PostersRole` returns a `PosterGridModel` whose `rowCount` and item roles match
  the row's items; a second `set_rows` resets cleanly.
- **`CatalogController`** (update existing test): inject a fake `BrowseCatalog`
  returning canned `CatalogRow`s; assert the rows model is populated and
  `loadingChanged` toggles. Update any test that assumed a flat
  `PosterGridModel`.
- **`BrowseCatalog`** (update existing test): assert the new `type` /
  `catalog_id` fields on returned rows.
- **QML** — no unit tests; verified at launch (offscreen platform in tests).
- **Gates:** `ruff check`, `ruff format --check`, `mypy --strict src`,
  `pytest` all green.

## Out of scope

- Filter dropdowns (type / catalog / genre).
- Genre filtering and catalog pagination (`skip`), catalog `extra` parsing.
- The Discover board page (step 2).
- Detail sidebar metadata enrichment (step 3).
- Any redesign of the app chrome (nav rail, search bar).
