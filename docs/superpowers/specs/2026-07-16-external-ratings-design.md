# Gravitas — External Ratings: Rotten Tomatoes + Letterboxd

**Date:** 2026-07-16
**Status:** Approved (brainstorming)

## Context

The Detail page shows a single rating — IMDb — because that is the only rating
the Stremio addon protocol carries (`meta.imdbRating`). Users want Rotten
Tomatoes and Letterboxd scores alongside it. Neither is in the protocol, and
Letterboxd has no public API, so both must come from an external ratings
aggregator keyed by the item's IMDb id.

The chosen source is **MDBList** (`api.mdblist.com`), which returns IMDb,
Rotten Tomatoes (critic %), Letterboxd, Metacritic, and more from one request.
It requires a free per-user API key.

This mirrors the existing external-metadata path (`ExternalIdResolver` /
`TmdbResolver` / `tmdb_key`) almost exactly.

## Decisions

| Topic | Decision |
|-------|----------|
| Source | MDBList, by IMDb id |
| API key | User pastes an MDBList key into Settings (new `mdblist_key`), mirroring `tmdb_key`. No key → IMDb only, no network call. |
| Which ratings | Add **Rotten Tomatoes** (critic %) and **Letterboxd** (/5). IMDb keeps coming from `meta` (instant); MDBList's IMDb value is ignored. |
| Loading | Separate, **non-blocking** async fetch after the Detail meta loads. Pills appear when it returns. The page never waits on or fails from it. |
| Fetch guard | Only fetch when the item id looks like an IMDb id (`tt…`) and a key is set. |
| Caching | 24h `TtlCache` keyed by imdb id (ratings move slowly), same analog as `META_TTL`. |
| Fault isolation | Any `GravitasError` / timeout / missing field → that pill (or all extra pills) is hidden. IMDb is unaffected. |
| Display | Branded pills next to the IMDb pill, each in native scale + brand color. RT red when fresh (≥60%), green when rotten (<60%). Letterboxd shows `/5` with brand dots. Missing scores render nothing. |
| Scope | Detail page only (not catalog grid). Movie + series. No Metacritic/Trakt, no per-item config. |

## MDBList response (assumptions to pin in TDD)

Endpoint (verify exact form against a live key during implementation):

```
GET https://api.mdblist.com/imdb/{movie|show}/{imdb_id}?apikey=KEY
```

The response contains a `ratings` array of objects shaped roughly:

```json
"ratings": [
  { "source": "imdb",       "value": 8.0,  "score": 80 },
  { "source": "tomatoes",   "value": 87,   "score": 87 },
  { "source": "letterboxd", "value": 4.1,  "score": 82 }
]
```

Scales: `imdb` 0–10, `tomatoes` 0–100 (percent), `letterboxd` 0–5. The exact
`source` strings (`tomatoes` vs `rottentomatoes`, etc.) and the letterboxd
scale **must be confirmed against a real response** before the parser is
finalized. The parser matches sources case-insensitively and treats any
unknown/absent source as missing, so a wrong guess degrades to "no pill" rather
than a crash. A recorded real JSON payload becomes the parser's test fixture.

## Data layer

### `Ratings` (domain/models.py)

New frozen, slotted dataclass — the value object a `RatingsResolver` returns:

```python
@dataclass(frozen=True, slots=True)
class Ratings:
    rotten_tomatoes: str | None = None       # e.g. "87" (percent, no sign)
    rotten_tomatoes_fresh: bool | None = None # True if >= 60, else False; None if absent
    letterboxd: str | None = None            # e.g. "4.1" (out of 5)
```

`Ratings()` (all `None`) is the empty/fallback value.

### `RatingsResolver` port (domain/ports.py)

```python
@runtime_checkable
class RatingsResolver(Protocol):
    async def ratings(self, imdb_id: str) -> Ratings: ...
```

### `MdbListUnavailable` (domain/errors.py)

`class MdbListUnavailable(GravitasError)` — raised by the adapter on network/HTTP
failure, mirroring `TmdbUnavailable`.

## Infrastructure

### `MdbListResolver` (infrastructure/metadata/mdblist_resolver.py)

- Constructor: `(http: httpx.AsyncClient, get_key: Callable[[], str | None])` +
  an internal `TtlCache[Ratings]` (24h). Same shape as `TmdbResolver`.
- `ratings(imdb_id)`:
  1. If no key → raise `MdbListUnavailable` (caller treats as empty). *(Callers
     also guard on key presence, so this is defensive.)*
  2. Cache hit → return cached `Ratings`.
  3. GET the endpoint (`follow_redirects=True, timeout=15.0`, `raise_for_status`);
     on any httpx error → `MdbListUnavailable`.
  4. Pure parse of the `ratings` array → `Ratings`. RT `rotten_tomatoes_fresh`
     computed from the percent (`>= 60`). Cache and return.
- Parsing is a small **pure function** (`_parse_ratings(payload) -> Ratings`),
  unit-tested independently of I/O — same split as `addons/parsing.py`.

## Application

### `GetRatings` (application/get_ratings.py)

```python
class GetRatings:
    def __init__(self, resolver: RatingsResolver) -> None: ...
    async def __call__(self, imdb_id: str) -> Ratings:
        try:
            return await self._resolver.ratings(imdb_id)
        except GravitasError:
            return Ratings()
```

Fault isolation lives here so the controller stays thin and always gets a valid
`Ratings`.

## Presentation

### `DetailController` (controllers/detail_controller.py)

- Holds `self._ratings: Ratings = Ratings()`; new signal `ratingsChanged`.
- New read-only Properties:
  - `rottenTomatoes: str` → `self._ratings.rotten_tomatoes or ""`
  - `rottenTomatoesFresh: bool` → `self._ratings.rotten_tomatoes_fresh or False`
  - `letterboxd: str` → `self._ratings.letterboxd or ""`
- In `load()`, after `_meta` is set and `metaChanged` emitted, reset
  `self._ratings = Ratings()`, emit `ratingsChanged`, then if
  `_get_ratings is not None` and `self._meta.id.startswith("tt")`, `await`
  `_load_ratings(self._meta.id)` (kept in the same asyncSlot; it is the last
  step so it never delays the meta render). `_load_ratings` sets `_ratings` and
  emits `ratingsChanged`.
- `GetRatings` is injected optionally (`get_ratings: GetRatings | None = None`)
  so existing controller tests need no change and QML with no key still works.

### `Detail.qml` (qml/Detail.qml)

In the existing meta `Row` (Detail.qml:88–126), after the IMDb pill add two
sibling pill groups:

- **Rotten Tomatoes** — `visible: detailController.rottenTomatoes.length > 0`.
  Score text `detailController.rottenTomatoes + "%"`, pill labelled `RT`, pill
  colour `detailController.rottenTomatoesFresh ? "#FA320A" : "#00A000"`.
- **Letterboxd** — `visible: detailController.letterboxd.length > 0`. Score text
  `detailController.letterboxd` (out of 5), dark pill `#14181C` with three brand
  dots (`#FF8000`, `#00E054`, `#40BCF4`) and a `Letterboxd` label.

Mirrors the existing IMDb pill structure; no new QML component required (a small
inline reuse is fine, matching today's inline IMDb pill).

### Settings (`SettingsController` + `Settings.qml`)

- `PersistedSettings` gains `mdblist_key: str | None = None`.
- `SettingsController` gains a second `_KeyHolder` (`mdblist_key_holder`),
  `mdblistKey` Property + `setMdblistKey` Slot + `mdblistKeyChanged` signal, and
  includes `mdblist_key` in `persist()`. Exact mirror of `tmdbKey`.
- `Settings.qml` gains an MDBList API key field beside the TMDB key field, with a
  short hint linking to where to get a free key.

## main.py wiring

```python
mdblist_key = _KeyHolder(persisted.mdblist_key)          # same holder type as tmdb_key
mdblist_resolver = MdbListResolver(http, lambda: mdblist_key.key)
get_ratings = GetRatings(mdblist_resolver)
detail_controller = DetailController(..., get_ratings=get_ratings)
settings_controller = SettingsController(..., mdblist_key_holder=mdblist_key)
```

`SettingsStore.save`/`load` round-trips the new `mdblist_key` field (sqlite/json
settings store updated accordingly).

## Error handling summary

| Situation | Result |
|-----------|--------|
| No MDBList key | No fetch; IMDb only |
| Non-`tt` id | No fetch; IMDb only |
| MDBList timeout / HTTP error | `MdbListUnavailable` → `GetRatings` returns `Ratings()` → no extra pills |
| `ratings` array missing a source | That pill hidden; others shown |
| Malformed value | Treated as missing (pure parser is defensive) |

## Testing (TDD)

- **domain** — `Ratings` defaults/immutability (`tests/domain/test_models.py`).
- **infrastructure** — `_parse_ratings` over a recorded MDBList payload:
  RT/Letterboxd extracted, fresh/rotten threshold, missing-source → `None`
  (`tests/infrastructure/metadata/test_mdblist_resolver.py`, respx for the HTTP
  path like `test_tmdb_resolver.py`); HTTP error → `MdbListUnavailable`.
- **application** — `GetRatings` returns parsed `Ratings`; swallows
  `GravitasError` into `Ratings()` (`tests/application/`).
- **presentation** — `DetailController` with a `FakeRatingsResolver`/`FakeGetRatings`
  exposes the new properties and skips the fetch for non-`tt` ids and when
  `get_ratings` is `None` (`tests/presentation/test_detail_controller.py`).
- **settings** — round-trip `mdblist_key` through the store; `setMdblistKey`
  persists.
- Gates: `ruff check`, `ruff format --check`, `mypy src` all green. QML verified
  at launch (no unit tests), per repo convention.

## Out of scope

Catalog-grid ratings, Metacritic/Trakt/other MDBList sources, per-item overrides,
resolving non-IMDb ids to IMDb for ratings (only `tt…` ids fetch).
