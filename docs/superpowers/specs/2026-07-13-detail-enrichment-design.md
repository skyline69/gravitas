# Gravitas — Detail Enrichment: Data + Detail Page (Step 3A)

**Date:** 2026-07-13
**Status:** Approved (brainstorming)

## Context

The Detail page currently shows only title, description, and a Sources stream
list. Stremio-style detail views show far more: the title *logo* art, a meta
row (runtime · year · IMDb rating), genre and cast chips, and directors. This
step enriches the domain `MetaDetail`, teaches the parser to read the extra
Cinemeta fields, exposes them to QML, and redesigns the Detail page — our own
visual design, not a Stremio clone.

This is **step 3A of 3B**. 3A delivers the shared data foundation plus the
Detail page. **3B** (its own spec) adds the same enriched view as a sidebar on
the Discover board, reusing everything 3A builds. 3B is out of scope here.

## Cinemeta `/meta` fields (verified)

A real Cinemeta `meta` object provides: `logo`, `background`, `name`,
`description`, `poster`, `genres` (list), `releaseInfo` (year string, e.g.
`"2004"`), `runtime` (string, e.g. `"37 min"`), `cast` (list), `director`
(list), `imdbRating` (string, e.g. `"9.0"`). All are optional in practice
(may be missing or empty).

## Decisions

| Topic | Decision |
|-------|----------|
| New meta fields | `logo`, `year`, `runtime`, `imdb_rating`, `genres`, `cast`, `directors` — all optional, defaulted |
| Year source | `releaseInfo` (string), falling back to `year` if present |
| Rating | `imdbRating` string shown with an "IMDb" badge |
| Controller exposure | `DetailController` exposes current meta as notifying QML properties via one `metaChanged` signal; streams stay in `stream_model` |
| Chips | New reusable `AppChip.qml` pill for genres/cast |
| Title art | Show `logo` image; fall back to the text title when `logo` is empty/fails |
| Design | Our own layout (see below), not a 1:1 Stremio copy |

## Data layer

### `MetaDetail` (domain/models.py)

Append optional, defaulted fields so existing constructions keep working:

```python
@dataclass(frozen=True, slots=True)
class MetaDetail:
    id: str
    type: MediaType
    name: str
    description: str | None
    poster: str | None
    background: str | None
    videos: tuple[Video, ...]
    logo: str | None = None
    year: str | None = None
    runtime: str | None = None
    imdb_rating: str | None = None
    genres: tuple[str, ...] = ()
    cast: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()
```

### `parse_meta` (infrastructure/addons/parsing.py)

Read the new fields tolerantly (missing → `None`/empty). `year` prefers
`releaseInfo`, then `year`. List fields (`genres`, `cast`, `director`) map to
string tuples, skipping non-strings. A small helper:

```python
def _str_or_none(v: Any) -> str | None:
    return str(v) if isinstance(v, (str, int, float)) and str(v) else None

def _str_tuple(v: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in v if isinstance(x, str)) if isinstance(v, list) else ()
```

`parse_meta` populates: `logo=_str_or_none(meta.get("logo"))`,
`year=_str_or_none(meta.get("releaseInfo")) or _str_or_none(meta.get("year"))`,
`runtime=_str_or_none(meta.get("runtime"))`,
`imdb_rating=_str_or_none(meta.get("imdbRating"))`,
`genres=_str_tuple(meta.get("genres"))`,
`cast=_str_tuple(meta.get("cast"))`,
`directors=_str_tuple(meta.get("director"))`.

## Presentation layer

### `DetailController`

Replace the narrow `titleChanged`/`descriptionChanged` signals with the full
meta surface. Hold the current `MetaDetail | None` and expose one
`metaChanged()` signal plus read-only QML `Property`s:

- strings: `title`, `description`, `poster`, `background`, `logo`, `year`,
  `runtime`, `imdbRating` (empty string when absent)
- QVariantList: `genres`, `cast`, `directors`
- bool: `hasMeta` (whether a meta is loaded)

`load(type, item_id)` sets the current meta from `GetDetail` (emitting
`metaChanged`), then resolves streams into `stream_model` as today. `errorOccurred`
unchanged. The bootstrap/`bind_manifest` path is unchanged.

Property typing follows the existing `DiscoverController` pattern (PySide6
`Property` with `# type: ignore[...]` codes as mypy reports; a private helper
per QVariantList property to avoid the intra-class `Property` typing quirk).

### `AppChip.qml` (new component)

A rounded pill: surface-tinted background, `Theme.text` label, `Theme.radius`
rounded, hover lightens. Property `text`. Used in horizontal-wrapping `Flow`s.

### `Detail.qml` redesign

Our layout, scrolled vertically:

- **Background:** the `background` image, dimmed/blurred behind the content
  (use `Qt5Compat.GraphicalEffects` `GaussianBlur`/`FastBlur` + a dark scrim
  overlay for readability).
- **Header:** the `logo` image centered (max height ~120, `PreserveAspectFit`),
  falling back to a bold text title when `logo` is empty or fails to load.
- **Meta row:** `runtime` · `year` · `imdbRating` with a small "IMDb" badge
  (a yellow rounded label). Items omitted when their value is empty.
- **Description:** wrapped `Theme.text`/`textDim`.
- **GENRES:** a dim section label + a `Flow` of `AppChip`s (hidden if empty).
- **CAST:** same pattern (hidden if empty).
- **Directed by:** a small line listing `directors` (hidden if empty).
- **Sources:** the existing `StreamRow` list + play, kept.

All bound to `detailController` properties (null-guarded like the Discover
combos). The page stays a pushed StackView page; navigation unchanged.

## Error handling

Unchanged: `GetDetail`/`ResolveStream` raise typed errors; `DetailController`
emits `errorOccurred`; the toast shows it. Missing meta fields render as
hidden/omitted sections, never errors.

## Testing

- **`parse_meta`:** extracts `logo`/`year`(from `releaseInfo`, and `year`
  fallback)/`runtime`/`imdbRating`/`genres`/`cast`/`directors`; missing fields
  → `None`/empty tuples; non-string list entries skipped.
- **`MetaDetail`:** new fields default correctly (existing constructions with
  7 args still valid).
- **`DetailController`:** a fake `GetDetail` returning an enriched `MetaDetail`
  populates every property and emits `metaChanged`; `hasMeta` toggles; error
  path emits `errorOccurred`. (Update the existing detail-controller test that
  asserted the old `titleChanged`/`descriptionChanged` signals.)
- **QML:** verified at launch; the composition test's `assert
  engine.rootObjects()` doesn't load `Detail.qml` (it's pushed at runtime), so
  add a headless `test_detail_qml_loads` (QQmlComponent load of `Detail.qml`
  with stub `detailController`/`streamModel`), mirroring the existing
  `test_discover_qml_loads`.
- **Gates:** `ruff check`, `ruff format --check`, `mypy --strict src`,
  `pytest` green each commit.
- **Manual:** launch, open a movie → logo, meta row, chips, directors, blurred
  background, streams all render; a title lacking a logo falls back to text.

## Out of scope

- The Discover board sidebar (step 3B).
- Series episode/season UI beyond the existing `videos` (unchanged here).
- TMDb/TVDB metadata override, trailers, "add to library"/watched actions
  (the image's extra action icons) — future milestones.
