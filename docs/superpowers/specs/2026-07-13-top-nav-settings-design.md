# Top Navigation Bar + Settings Page — Design

Date: 2026-07-13

## Goal

Replace the ad-hoc addon-manifest input strip at the top of `Home.qml` with a
proper application navigation bar, and give the app a Settings page.

The nav bar carries content-type tabs (**All · Movies · Series · Trending**)
that filter the Home board, plus a Settings gear at the top-right. Settings
hosts addon management (add + list + remove) and a static About section.

## Scope

In scope:

- Persistent top navigation chrome around the main `StackView`.
- Four Home tabs that filter the aggregated catalog rows by content type
  (plus a keyword-based "Trending" view).
- A Settings page: addon add/list/remove, plus About.
- Making the bootstrapped default addon (Cinemeta) non-removable.

Out of scope (deferred, unchanged by this work):

- Persistence of installed addons. The addon store stays in-memory; Cinemeta
  is re-bootstrapped on every launch. Removing a user-added addon does not
  survive a restart either.
- Themes, debrid, search, continue-watching, and every other roadmap item.

## Architecture

The clean-architecture dependency rule is preserved
(`presentation → application → domain ← infrastructure`). New pieces:

- **domain / application:** `AddonRepository.uninstall` + protected-addon
  tracking; a new `UninstallAddon` use case.
- **presentation:** a new `TopBar.qml` and `Settings.qml`, a new
  `SettingsController`, a new `AddonListModel`, and a `set_filter` capability
  on the existing `CatalogRowsModel`.
- **composition root:** `main.py` wires the new use case, model, and
  controller, and marks the default addon protected at bootstrap.

## Components

### 1. Nav chrome — `TopBar.qml` (new), `Main.qml` (edit)

`TopBar` is a themed bar (~56px, `Theme.surface`) placed in `Main.qml` so it
wraps the `StackView` rather than living inside any page.

- **Left:** four tabs `All · Movies · Series · Trending` rendered as themed
  segments (active state highlighted — pill or underline, consistent with the
  existing component set). Clicking a tab sets the active filter.
- **Right:** a Settings gear (`AppIcon` with a Phosphor glyph) that pushes the
  Settings page.

Visibility:

- Visible on **Home** and **Settings**.
- Hidden on **Player** (fullscreen playback).
- Hidden on **Detail** (Detail already has its own back button / chrome).

`Main.qml` drives visibility from the current `StackView` page (e.g. bind to
`stack.currentItem`/`depth` or an explicit page tag).

Tab behaviour when not on Home: clicking a tab pops the StackView back to Home,
then applies the filter — a tab click always lands the user on a filtered Home.

The active tab is tracked in `Main.qml` (or `TopBar`) and defaults to **All**.

### 2. Tab filtering — `CatalogRowsModel` (edit), `CatalogController` (edit)

Filtering happens in the Python presentation model, not in QML — the model is
unit-tested (QML is not), and QML stays declarative.

`CatalogRowsModel` retains **all** rows internally and computes a filtered view:

- New method `set_filter(mode)` with `mode ∈ {"all", "movie", "series", "trending"}`.
- `all` → all rows.
- `movie` / `series` → rows whose `type == mode`.
- `trending` → rows whose `title` **or** `catalog_id` contains any of
  `top`, `trending`, `popular` (case-insensitive), regardless of type.
- `rowCount` / `data` operate over the filtered index, mapping filtered row →
  underlying row.
- `set_rows(...)` re-applies the current filter after rebuilding.
- Filter changes and row changes emit correct model-reset signals so the QML
  `ListView` refreshes.
- Default filter is `all`.

`CatalogController` gains a plain `@Slot(str) def setFilter(mode)` that forwards
to the model. (Plain `@Slot`, not `asyncSlot` — it does no I/O, only re-filters
already-loaded rows.)

### 3. Settings page — `Settings.qml` (new)

Pushed onto the `StackView` when the gear is clicked. Sections:

**Addons**

- Manifest URL `AppTextField` + `Add` `AppButton`, moved verbatim from the old
  Home addon bar. Calls `addonController.addAddon(url)` and clears the field.
- Installed-addon list, fed by `AddonListModel`. Each row shows the addon name
  and, when removable, a remove button. Clicking remove calls
  `settingsController.removeAddon(id)`.
- The protected default addon (Cinemeta) renders with **no** remove button.

**About**

- Static content: app name ("Gravitas"), version, a one-line description, and a
  repo link. No dynamic data.

A back button returns to Home.

### 4. Installed-addon model — `AddonListModel` (new)

A `QAbstractListModel` subclass (mirrors the existing model style):

- Roles: `NameRole`, `IdRole`, `RemovableRole`.
- `set_addons(list[AddonManifest], protected_ids)` builds rows; `removable` is
  `id not in protected_ids`.
- Standard `rowCount` / `data` / `roleNames`.

### 5. Remove addon — repository + use case

**`AddonRepository` (edit)**

- Add `self._protected: set[str]`.
- `install(url, *, protected: bool = False)` — records the id in `_protected`
  when `protected=True`.
- `uninstall(addon_id: str)` — removes the manifest; raises `GravitasError`
  when the id is protected or not installed.
- `is_protected(addon_id: str) -> bool`.

**`UninstallAddon` (new use case)**

Mirrors `InstallAddon`: takes the repository, exposes an async/sync call that
uninstalls by id and surfaces `GravitasError` on failure. Keeps the controller
free of direct repository calls, consistent with the existing addon path.

### 6. Settings controller — `SettingsController` (new)

QObject bridge:

- Holds `UninstallAddon`, `AddonRepository`, `AddonListModel`, and the catalog
  controller (for post-change refresh).
- `refreshAddons()` — repopulate `AddonListModel` from `repo.installed()` +
  `repo.is_protected`, emit `addonsChanged`.
- `removeAddon(id)` slot — run `UninstallAddon`, then refresh the catalog rows
  (same `load_catalog()` path the install flow uses) and the addon list.
- `errorOccurred(str)` signal, surfaced as a toast like the other controllers.

The existing `addonController.addAddon` flow also needs to refresh the addon
list after a successful install (so a newly added addon appears in Settings) —
either by having `SettingsController` listen to `addonInstalled` and call
`refreshAddons()`, or by refreshing the list in the same flow.

### 7. Composition root — `main.py` (edit)

- Construct `UninstallAddon`, `AddonListModel`, `SettingsController`.
- Expose them (and anything QML binds) as context properties, held on
  `engine._gravitas_refs` to prevent GC.
- Bootstrap installs Cinemeta via `repo.install(url, protected=True)` so it is
  non-removable.
- After bootstrap, prime `SettingsController.refreshAddons()` so the initial
  installed list is populated.

### 8. Home page — `Home.qml` (edit)

- Remove the `addonBar` (URL field + Add button) — it now lives in Settings.
- Home becomes the rows `ListView` + centered `AppSpinner` only, anchored to
  the top of its own area (the nav bar sits above it, provided by `Main.qml`).

## Data flow

- **Filter:** tab click → `Main`/`TopBar` sets active tab →
  `catalogController.setFilter(mode)` → `CatalogRowsModel.set_filter` →
  model reset → Home `ListView` shows filtered rows. No network I/O.
- **Add addon:** Settings field → `addonController.addAddon(url)` →
  `InstallAddon` → repo install → catalog reload → `addonInstalled` →
  Settings list refresh.
- **Remove addon:** Settings remove button →
  `settingsController.removeAddon(id)` → `UninstallAddon` → repo uninstall →
  catalog reload + addon-list refresh. Protected id → `GravitasError` → toast.

## Error handling

- `uninstall` on a protected or missing id raises `GravitasError`; the
  controller emits `errorOccurred`, shown as a toast (Cinemeta has no remove
  button, so this is a guard, not an expected path).
- `addAddon` errors keep their existing toast behaviour.
- Filtering cannot fail (pure in-memory transform).

## Testing

Python (unit-tested; QML verified at launch only):

- `CatalogRowsModel.set_filter`: `all`, `movie`, `series`, `trending`
  (keyword match on title and on catalog_id, case-insensitivity, no-match →
  empty), and `set_rows` re-applying the active filter.
- `AddonRepository`: `uninstall` happy path; `uninstall` protected id raises;
  `uninstall` absent id raises; `is_protected` reflects the `protected=True`
  install flag.
- `UninstallAddon` use case: success and error propagation.
- `SettingsController` / `AddonListModel`: list populated from installed +
  protected ids; `removable` flag correct; `removeAddon` triggers refresh.

QML (`TopBar.qml`, `Settings.qml`, `Home.qml`, `Main.qml`): verified at app
launch — tab switching filters rows, gear opens Settings, add/remove work,
Cinemeta shows no remove button.

Quality gates (`ruff check`, `ruff format --check`, `mypy src`) pass.

## Deferred / follow-ups

- Persist installed addons across launches (currently in-memory).
- Themes, debrid, search, series episode UI — tracked elsewhere.
