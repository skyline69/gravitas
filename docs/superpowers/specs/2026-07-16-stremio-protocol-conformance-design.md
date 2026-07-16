# Stremio protocol conformance — batch 1–4

Status: approved, ready to implement
Date: 2026-07-16

## Why

An audit against `Stremio/stremio-addon-sdk` found Gravitas implements the
protocol's spine (catalog / meta / stream, with genre, skip and search) but
diverges from it in ways that cost real behaviour. This spec covers the four
mechanical milestones. Deep links, `addon_catalog` and `subtitles` get their own
specs.

Findings were verified against the live Cinemeta addon rather than from the docs
alone; where the two disagree, the probe wins and is quoted below.

## Milestones in this batch

1. Stream fidelity — `description`, `ytId`, `externalUrl`, `proxyHeaders`
2. Routing — `idPrefixes`, `types`, per-resource filters
3. Extra conformance — `isRequired` / `extraRequired`, `optionsLimit`
4. Meta fidelity — `links`, `posterShape`, trailers, `defaultVideoId`

## Evidence that shaped the design

- Cinemeta declares `idPrefixes: ["tt"]` and `resources: [catalog, meta,
  addon_catalog]` — note it serves **no** streams.
- Four Cinemeta catalogs declare required extras: `movie/year` and `series/year`
  require `genre`; `series/last-videos` requires `lastVideosIds`;
  `series/calendar-videos` requires `calendarVideosIds`.
- Cinemeta **tolerates** missing required extras: all eight catalogs return
  items when requested bare. So `isRequired` is a conformance gap, not a live
  outage — but `last-videos` and `calendar-videos` return 100 arbitrary items
  each, which Home currently renders as junk rows. That is a live bug.
- Cinemeta meta emits **both** the legacy `genres`/`cast`/`director` arrays and
  the modern `links` array. Deriving from `links` is therefore pure gain with no
  regression risk.
- `trailerStreams` is `[{title, ytId}]`, so trailers depend on `ytId` playback.
- `behaviorHints.defaultVideoId` is set for movies (`tt0133093`), null for
  series.
- `posterShape` is absent from Cinemeta meta; the protocol default is `poster`.

## 1. Domain (`domain/models.py`)

Two new frozen value objects replace today's flattened strings:

```python
@dataclass(frozen=True, slots=True)
class ExtraSpec:
    name: str
    is_required: bool = False
    options: tuple[str, ...] = ()
    options_limit: int = 1

@dataclass(frozen=True, slots=True)
class ResourceSpec:
    name: str
    types: tuple[str, ...] = ()        # empty = inherit manifest.types
    id_prefixes: tuple[str, ...] = ()  # empty = inherit manifest.id_prefixes
```

`Stream` gains `yt_id`, `external_url` and `proxy_headers`, and splits the
single `is_direct` flag into explicit intent:

- `playable_url` — `url`, else a YouTube watch URL built from `yt_id`, else None
- `is_direct` — `playable_url is not None` (what `ResolveStream` filters on)
- `is_external` — no playable URL but an `external_url` (browser hand-off)

`infoHash` streams keep resolving to `is_direct == False`. The no-torrent rule
is untouched.

`AddonManifest.resources` becomes `tuple[ResourceSpec, ...]` and the manifest
gains `id_prefixes` plus:

```python
def serves(self, resource: str, type: MediaType | None = None, id: str | None = None) -> bool
```

Rules, per protocol: the resource must be declared; `type` must be in the
resource's own `types` or, absent those, the manifest's `types`; `id` must start
with one of the resource's `id_prefixes` or, absent those, the manifest's.
An empty prefix list means no filtering. **`idPrefixes` never applies to
`catalog`.**

`CatalogRef` carries `extra: tuple[ExtraSpec, ...]`. `genres`, `supports_skip`
and `supports_search` stay as derived properties so existing call sites keep
working. New: `required_extras` and `is_browsable`.

`MediaItem` and `MetaDetail` gain `poster_shape: PosterShape` (`Literal["poster",
"landscape", "square"]`, default `"poster"`). `MetaDetail` also gains `writers`,
`trailer_yt_id` and `default_video_id`.

## 2. Parsing (`infrastructure/addons/parsing.py`)

- `parse_manifest` builds `ResourceSpec`s from both the string and object forms,
  reads `idPrefixes`, and builds `ExtraSpec`s from `extra`, falling back to the
  legacy `extraSupported` / `extraRequired` / `genres` triple.
- `parse_streams` sets display text from `description → title → name` (the SDK
  deprecates `title` in favour of `description`), and reads `ytId`,
  `externalUrl` and `behaviorHints.proxyHeaders`.
- `parse_meta` derives `genres` / `cast` / `directors` / `writers` from `links`
  **only when the legacy arrays are absent**; reads `posterShape`,
  `behaviorHints.defaultVideoId`, and the trailer id from
  `trailerStreams[0].ytId` falling back to `trailers[0].source`.
- `parse_catalog` reads `posterShape` per item.
- The deliberate drop of `channel` / `tv` catalogs gets a comment naming it as a
  product decision, not an oversight.

## 3. Routing (`application/addon_repository.py`)

`meta()` and `streams()` filter candidate addons through `manifest.serves(...)`
instead of `"meta" in manifest.resources`. This stops Cinemeta being asked for
`kitsu:` ids and stops movie-only addons being asked for series.

`catalog_refs()` and `catalog_options()` drop non-browsable catalogs. A catalog
is browsable when every required extra is one Gravitas can supply: `genre` (with
options — pass the first) or `skip`. `lastVideosIds` and `calendarVideosIds`
cannot be supplied, so those catalogs disappear from Home and Discover — fixing
the junk rows.

`fetch_catalog` passes the first genre option when `genre` is required and none
was given.

## 4. Player + presentation

`proxyHeaders` needs a path to mpv, the one place this batch reaches beyond
parsing:

- `MediaPlayer.play()` takes optional `headers`; `MpvPlayer` maps them onto
  mpv's `http-header-fields`.
- `ResolveStream` returns the `Stream`, not a bare URL, so headers survive.
- `PlayerController.play` accepts headers; the `playUrl` signal carries them
  through `Detail.qml` / `Sources.qml` / `Player.qml`.

Presentation:

- `PosterCard` derives its aspect ratio from the `poster_shape` role
  (2:3 / 16:9 / 1:1).
- External-only streams get an open-in-browser affordance (`QDesktopServices`).
- Detail shows a trailer action when `trailer_yt_id` is set, and a writers row.
- Discover preselects the first genre for genre-required catalogs.

`ytId` playback relies on mpv's `ytdl_hook`, which needs `yt-dlp` on PATH. Like
libmpv it is an optional system dependency: absent, YouTube-backed streams fail
with the existing `PlaybackFailed` path. To be documented in CLAUDE.md.

## Non-goals (deliberate, with reasons)

- **Torrent / `infoHash` playback** — `CLAUDE.md` forbids a torrent engine.
  Streams keep being filtered by `is_direct`. Debrid remains a future milestone
  and the `DebridResolver` port stub stays unused.
- **`channel` / `tv` types** — would make `MediaType` four-way and turn Gravitas
  into an IPTV client. Documented and surfaced, not implemented.
- **`bingeGroup`** — inert until autoplay-next exists.
- **`countryWhitelist`** — needs geo knowledge the app does not have.
- **`notWebReady`** — meaningless to mpv, which plays what browsers will not.
- **`sources` (trackers)** — no torrent engine.
- **`optionsLimit`** — parsed and stored; the genre UI stays single-select.
- **`links` of category `imdb` / `share`** — no link-out UI exists to host them.

## Testing

- Parsing: fixtures captured from the real Cinemeta manifest and meta responses,
  plus synthetic manifests covering `idPrefixes`, per-resource `types`, the
  object/string resource forms, required extras, and the legacy
  `extraSupported` / `extraRequired` shape.
- Domain: `serves()` truth table, `is_browsable`, `playable_url` / `is_direct` /
  `is_external` (including that `infoHash` stays non-direct).
- Routing: fake-source tests proving a prefix-mismatched addon is never called
  and non-browsable catalogs never reach Home.
- Player: `FakeMpv` asserts `http-header-fields` is set from `proxy_headers`.
- Everything offline. No network in the suite.
