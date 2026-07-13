# Discover Filter Bar Redesign

Date: 2026-07-14
Status: Approved

## Problem

The Discover page's filter row (`Discover.qml`) is a plain unstyled `Row` holding a Back button and three combo boxes. It clashes with the app's floating-pill `TopBar`, offers no way to narrow the loaded catalog, and has no motion language.

## Goals

- Match the TopBar's floating-pill visual language.
- Add an inline text filter that live-narrows the loaded catalog by title.
- Add client-side sorting of loaded items.
- Replace the type combo with an animated segmented control.
- Show a result count with a loading shimmer.
- Provide a one-click reset when any filter deviates from its default.
- No dead band between the bar and the grid: the grid scrolls underneath the bar.

## Non-goals

- Server-side search or sort. Stremio catalogs arrive pre-sorted; sorting applies to loaded items only.
- Changes to `DiscoverController` behavior, pagination, or the addon protocol.
- Hiding the bar on scroll.

## Design

### Bar layout

A floating pill identical in language to `TopBar`: `Theme.surface` fill, `radius: Theme.radius * 2`, 1 px `Theme.borderStrong` border, height 56, `z: 1`, horizontally centered, `width: Math.min(parent.width - 24, 1040)`, 12 px top margin. The grid fills the whole page and scrolls under the bar; the grid's `topMargin` insets the first row below the bar.

Left to right:

```
[←] [ Movies | Series ] [ Popular (Cinemeta) ▾ ] [ All genres ▾ ] [⌕ filter…] [Sort ▾] [×] ··· [128 titles]
```

### Controls

- **Back** — existing ghost `AppButton`, icon-only with a "Back" tooltip.
- **Type** — new `SegmentedControl.qml` component: a single pill background with one sliding highlight `Rectangle` that animates its `x` behind the active segment. Model comes from `discoverController.typeOptions`; the control handles any segment count, since addons define their own types.
- **Catalog and genre** — existing `AppComboBox`. The genre combo labels its default entry "All genres".
- **Filter field** — compact text field with a magnifier icon and placeholder "Filter this catalog". Each keystroke updates the proxy filter directly (no debounce; filtering is local and cheap). Escape clears and blurs. The field expands in width on focus, mirroring the TopBar search behavior.
- **Sort** — `AppComboBox` with Default, Name, Newest, Rating. Sorts loaded items client-side via the proxy.
- **Reset** — small ghost × button, visible only when genre differs from default, filter text is non-empty, or sort is not Default. Clicking resets all three. Fades and scales in/out.
- **Count** — right-aligned dim label bound to the proxy's row count: "128 titles", or "12 / 128" while a filter is active. While `discoverController` reports loading, the label is replaced by a shimmer bar (same pulse as the SearchBar skeleton). The centered `AppSpinner` remains only for the initial empty load.

### Python changes

- `PosterGridModel` gains `YearRole` and `RatingRole`, sourced from the existing `MediaItem.year` and `MediaItem.imdb_rating` fields.
- New `presentation/models/poster_grid_proxy.py`: `PosterGridProxy(QSortFilterProxyModel)` exposing slots `setFilterText(str)` and `setSortKey(str)` with keys `default | name | year | rating`.
  - Filter: case-insensitive substring match on name.
  - Sort: year and rating parse to numbers; items missing the value sort last. `name` sorts case-insensitively. `default` restores source order (dynamic sorting off).
- `main.py`: wrap the discover `PosterGridModel` in a `PosterGridProxy`, expose it as the `discoverProxy` context property, and keep it alive on `engine._gravitas_refs`.
- `Discover.qml`: grid binds to `discoverProxy`; the controller and pagination are untouched. Filtering and sorting are purely presentation concerns.

### Motion

All durations and easings come from `Theme`:

- Segmented highlight slides with `Easing.OutCubic` over `Theme.durMed`.
- Filter field width expands on focus over `Theme.durMed`.
- Reset button fades and scales over `Theme.durFast`.
- Count label crossfades on change; shimmer pulses while loading.
- `GridView` `add` and `displaced` transitions: fade plus a slight upward rise over `Theme.durMed`, so filtering reads as movement rather than a repaint.

## Error handling

No new failure modes: the proxy is synchronous and local. Existing null-guards on `discoverController` in `Discover.qml` stay, since `StackView` teardown can momentarily null the context property.

## Testing

New `tests/presentation/test_poster_grid_proxy.py`:

- filter narrows rows and is case-insensitive
- clearing the filter restores all rows
- each sort key orders correctly; missing year/rating sorts last
- `default` preserves source order after a previous sort
- row count tracks filter changes

QML remains launch-verified per project convention. The three quality gates (`ruff check`, `ruff format --check`, `mypy src`) must pass.
