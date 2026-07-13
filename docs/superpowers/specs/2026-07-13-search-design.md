# Search — Design

Date: 2026-07-13

## Goal

Add a searchbar to the center of the floating top bar that:

- Searches movies/series across installed Stremio addons (addon search catalogs).
- Resolves a pasted IMDB or TVDB link/id to a specific title.
- Shows a **live preview** dropdown (poster + title + year) as the user types,
  and a full results grid on Enter.
- Is animated throughout (searchbar focus-expand, dropdown enter/exit, hover).

## Scope

In scope:

- `supports_search` parsing + a `search` query path for catalogs.
- Cross-addon search aggregation with per-addon fault isolation, dedup, and a cap.
- IMDB link/id resolution via addon `/meta` (native).
- TVDB link/id resolution via the TMDB `/find` API (maps to an IMDB id so the
  normal Detail/stream flow works). TMDB API key entered in Settings.
- Live dropdown preview + a full search-results grid page.
- Animations: searchbar width expand on focus, dropdown scale+fade+slide,
  row hover, loading spinner.

Out of scope (deferred):

- Deep pagination of search results. v1 aggregates the first page from each
  searchable catalog, dedups, and caps the total.
- Persistence of the TMDB API key and of installed addons (both in-memory this
  session — persistence is a separate roadmap item).
- TMDB-based metadata override for normal browsing.

## Architecture

Clean Architecture is preserved (`presentation → application → domain ←
infrastructure`). New/changed pieces per layer:

- **domain**: `CatalogRef.supports_search`, `MediaItem.year`, an
  `ExternalIdResolver` port + `ResolvedMedia` result, a `TmdbUnavailable` error.
- **application**: `AddonRepository.search`, `SearchMedia`, `ResolveMediaLink`,
  `parse_media_link`.
- **infrastructure**: `search` path in the addon client/parsing, a
  `TmdbResolver` adapter.
- **presentation**: `SearchController`, `SearchResultsModel`, `SearchBar.qml`,
  `SearchResults.qml`, TopBar integration, a TMDB key field in Settings.
- **composition root**: wire it all in `main.py`.

## Components

### 1. Domain changes

- `CatalogRef` gains `supports_search: bool = False`.
- `MediaItem` gains `year: str | None = None` (default keeps every existing
  construction valid; frozen dataclass, backward compatible).
- New port in `domain/ports.py`:

  ```python
  class ExternalIdResolver(Protocol):
      async def resolve(self, source: str, external_id: str) -> ResolvedMedia: ...
  ```

  `source` is `"imdb"` or `"tvdb"`.
- New result model in `domain/models.py`:

  ```python
  @dataclass(frozen=True, slots=True)
  class ResolvedMedia:
      imdb_id: str
      type: MediaType
      name: str
      poster: str | None
      year: str | None
  ```
- New error in `domain/errors.py`: `class TmdbUnavailable(GravitasError)` —
  raised when a TVDB resolution is attempted without a key, or TMDB fails.

### 2. Addon search path (infrastructure/parsing + client)

- `parse_manifest` marks a `CatalogRef.supports_search = True` when the raw
  catalog's `extraSupported` (or legacy `extra`) contains `"search"`.
- `catalog_path_extra(ref, genre, skip, search)` appends `search={quoted}` to
  the `/catalog/{type}/{id}/…` extra segment. URL-encode the query.
- `AddonClient.fetch_catalog(manifest, ref, *, genre=None, skip=0, search=None)`
  passes `search` through. The `AddonSource` Protocol signature gains the same
  keyword.

### 3. Repository search (application)

`AddonRepository.search(query: str) -> list[MediaItem]`:

- Iterate every installed addon's catalogs where `ref.supports_search`.
- For each, `fetch_catalog(manifest, ref, search=query)`, fault-isolated (log +
  skip on `GravitasError`, like `aggregate_catalog`).
- Concatenate, dedup by `MediaItem.id` (keep first), cap at 60.

### 4. Use cases (application)

- `SearchMedia(repo)`: `async def __call__(self, query: str) -> list[MediaItem]`
  → `repo.search(query)` (empty/whitespace query → `[]`).
- `parse_media_link(text) -> tuple[str, str] | None` (pure, in
  `application/media_links.py`): returns `("imdb", "tt1234567")` or
  `("tvdb", "12345")` or `None`. Recognizes:
  - `tt\d+` anywhere (raw id or imdb.com/title/tt…/ url),
  - `thetvdb.com/…/series/…` or `thetvdb.com/?tab=series&id=123` or a
    `tvdb:12345` / bare-numeric-with-tvdb-context form. Keep the matcher
    conservative and documented; unit-tested against concrete strings.
- `ResolveMediaLink(repo, resolver)`:
  `async def __call__(self, source: str, external_id: str) -> MediaItem`:
  - `imdb`: try `repo.meta("movie", id)`, on failure `repo.meta("series", id)`;
    build `MediaItem(id, type, name, poster, year)` from the `MetaDetail`.
  - `tvdb`: `resolver.resolve("tvdb", id)` → `ResolvedMedia`; return
    `MediaItem(id=resolved.imdb_id, type, name, poster, year)`.
  - No match / resolver failure → raise the propagated `GravitasError`
    (`NoStreams`-style `AddonUnreachable`/`TmdbUnavailable`).

### 5. TMDB resolver (infrastructure/metadata/tmdb_resolver.py)

`TmdbResolver` implements `ExternalIdResolver`:

- Constructed with an httpx `AsyncClient` and a key accessor
  `get_key: Callable[[], str | None]` (so the key can be set later from
  Settings without rebuilding the adapter).
- `resolve(source, external_id)`:
  - If `get_key()` is falsy → raise `TmdbUnavailable("add a TMDB API key in Settings")`.
  - `GET https://api.themoviedb.org/3/find/{external_id}?external_source={source}_id&api_key={key}`.
  - Pick the first of `movie_results` / `tv_results`; map to `type`
    (`movie` / `series`).
  - For an IMDB find the imdb id is the input; for a TVDB find, read the imdb id
    from `GET /{movie|tv}/{tmdb_id}/external_ids` (`imdb_id`). If absent → raise
    `TmdbUnavailable`.
  - Build poster url `https://image.tmdb.org/t/p/w342{poster_path}`, year from
    `release_date`/`first_air_date` (first 4 chars).
  - Network/parse failures → `TmdbUnavailable`.

### 6. Presentation

**`SearchResultsModel` (QAbstractListModel)** — roles `IdRole`, `TypeRole`,
`NameRole`, `PosterRole`, `YearRole` (QML: `mediaId`, `type`, `name`, `poster`,
`year`). `set_items(list[MediaItem])`. Feeds both the dropdown and the full page.

**`SearchController` (QObject)**:

- Holds `SearchMedia`, `ResolveMediaLink`, `SearchResultsModel`.
- `errorOccurred(str)`, `loadingChanged(bool)`, `resultsChanged()` signals.
- `@Slot(str) queueSearch(text)`: debounce via an internal `QTimer` (350 ms
  single-shot); on fire, run the async worker.
- Async worker (`@asyncSlot`): if `parse_media_link(text)` → `ResolveMediaLink`
  → `set_items([item])`; else `SearchMedia(text)` → `set_items(results)`.
  Guard stale results with a monotonically increasing request id (same pattern
  as the detail-load race fix), so an older query never clobbers a newer one.
- `@Slot() clear()`: cancel timer, clear model.

**`SearchBar.qml`** (in TopBar center):

- `AppTextField`-based input + a leading `Icons.search` glyph.
- Resting width (e.g. 220) animates to a wider focused width (e.g. 360) via
  `Behavior on width` when `activeFocus`.
- On text change → `searchController.queueSearch(text)`; on focus-lost with
  empty text → collapse.
- Dropdown: a themed `Popup` below the field, shown when there are results (or
  loading) and the field has focus. Enter/exit transitions = scale+fade+slide
  (same as `AppToolTip`). Rows: poster thumb + title + year, hover wash,
  click → `openDetail(type, mediaId)` (bubbled to Main, which pushes Detail).
  Spinner while `loading`; "No results" text when a completed non-link query
  yields nothing.
- Enter key → bubble `openResults(query)` to Main → push `SearchResults` page.

**`SearchResults.qml`** (page, `objectName: "searchResultsPage"`): a poster grid
(reuse the `PosterCard`/grid pattern from Discover) bound to
`searchResultsModel`; back button; `onOpenDetail` bubbles up. TopBar stays
visible on this page (add its objectName to the bar's visibility set) so search
stays accessible, or hidden — decision: **keep the bar visible** so a new search
can be started, and treat this like Home for bar visibility.

**TopBar layout**: three zones — tabs (left), `SearchBar` (center, via
`anchors.horizontalCenter`), gear (right). The bar's max width grows to
accommodate (raise the `Math.min(..., 820)` cap to ~1040) so the center bar has
room; still centered and floating.

**Settings**: add a "Playback / Metadata" style section (or under Addons) with a
**TMDB API key** `AppTextField` → `settingsController.setTmdbKey(text)`.
Placeholder notes it's optional (only needed for TVDB links).

### 7. Composition root (main.py)

- Build `TmdbResolver(http, get_key=lambda: tmdb_key_holder.key)` where a tiny
  mutable holder object stores the key; `SettingsController.setTmdbKey` sets it.
- Build `SearchMedia(repo)`, `ResolveMediaLink(repo, resolver)`,
  `SearchResultsModel`, `SearchController(...)`.
- Context properties: `searchController`, `searchResultsModel`. Add both to the
  `_gravitas_refs` keep-alive tuple. Pass the key holder to `SettingsController`.

## Data flow

- **Type query** → `SearchBar` → `queueSearch` (debounced) → not a link →
  `SearchMedia` → `repo.search` (searchable catalogs, fault-isolated, dedup,
  cap) → `SearchResultsModel` → dropdown updates. Enter → `SearchResults` page.
- **Paste imdb link** → `queueSearch` → `parse_media_link` → `ResolveMediaLink`
  (imdb) → `repo.meta` movie/series → single preview item → dropdown. Click →
  Detail (imdb id already native).
- **Paste tvdb link** → `ResolveMediaLink` (tvdb) → `TmdbResolver.resolve` →
  imdb id + title/poster/year → preview item keyed by imdb id → Detail/streams
  work natively. No key → `TmdbUnavailable` → toast.

## Error handling

- Per-addon search failures are logged and skipped (aggregation continues).
- `ResolveMediaLink`/`TmdbResolver` failures propagate as `GravitasError`;
  `SearchController` emits `errorOccurred` → toast. Missing TMDB key →
  friendly `TmdbUnavailable` message.
- Stale-response guard (request-id) prevents an older query/resolution from
  overwriting a newer one.

## Testing

Python (unit-tested):

- `parse_manifest`: `supports_search` true when `extraSupported`/`extra` has
  `search`, false otherwise.
- `catalog_path_extra` with `search` (URL-encoding, combined with genre/skip).
- `AddonClient.fetch_catalog(search=…)` builds the right path (fake transport).
- `AddonRepository.search`: aggregation across searchable catalogs, skips
  non-searchable, fault-isolation, dedup by id, cap.
- `SearchMedia`: empty query → `[]`; delegates otherwise.
- `parse_media_link`: imdb url, raw `tt…`, tvdb url, `tvdb:…`, plain text → None.
- `ResolveMediaLink`: imdb movie hit; imdb movie-miss→series-hit; tvdb via fake
  resolver; error propagation.
- `TmdbResolver`: no key → `TmdbUnavailable`; find→external_ids happy path
  (fake http) → `ResolvedMedia`; malformed → `TmdbUnavailable`.
- `SearchResultsModel`: roles/rows.
- `SearchController`: link vs search dispatch, stale-request guard, `clear`,
  loading signal. (Debounce timer can be exercised by calling the worker
  directly.)

QML (`SearchBar.qml`, `SearchResults.qml`): in-process load tests with stub
context properties; live-verified for animation/behavior.

Quality gates (`ruff check`, `ruff format --check`, `mypy src`) pass; full
suite green.

## Deferred / follow-ups

- Deep search pagination / infinite scroll on the results page.
- Persist the TMDB key + installed addons across launches.
- TMDB metadata override for browsing.
