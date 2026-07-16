# Gravitas — MVP Design

**Date:** 2026-07-12
**Status:** Approved (brainstorming)

Gravitas is a memory-efficient, Linux-first desktop media center and an
alternative to Stremio/Chillio/Fusion. It consumes Stremio addons and never
runs a torrent engine itself: torrents become direct HTTP URLs either
addon-side (the addon is configured with the user's debrid key) or, in a later
milestone, client-side via debrid-service APIs.

## Decisions

| Topic | Decision |
|-------|----------|
| Stack | Python + PySide6 (Qt6/QML) + libmpv (`python-mpv`) |
| HTTP | `httpx` async client |
| Packaging / env | `uv` (lock, deps, venv) |
| Quality gates | `ruff` (lint + format), `mypy --strict`, `pytest` |
| Architecture | Clean Architecture — layered dependency rule |
| MVP loop | Add addon → browse catalog → detail → pick stream → play |
| Metadata source (MVP) | Addon-provided (Stremio `/meta`, Cinemeta-style). No API keys. |
| Torrenting | Never implemented in-app. Direct URLs only. |
| Debrid (MVP) | Addon-side only (addon returns unlocked direct URLs) |
| Debrid (later) | Client-side pluggable `DebridResolver`, one adapter per service |
| MVP streams | Direct HTTP/HLS URLs only |

## Architecture

Strict layered dependency rule:
`presentation → application → domain ← infrastructure`.

The domain layer imports no framework code (no Qt, no httpx). Infrastructure
adapters implement ports (Python `Protocol`s) defined by the inner layers. The
composition root (`main.py`) is the only place that references concrete
adapter classes.

```
gravitas/
  pyproject.toml            # uv-managed; ruff + mypy(strict) + pytest config
  src/gravitas/
    domain/                 # pure: entities + port interfaces, no deps
      models.py             # MediaItem, MetaDetail, Stream, AddonManifest (dataclasses)
      ports.py              # Protocols: AddonSource, DebridResolver, MediaPlayer, Cache
    application/            # use cases, depend only on domain ports
      browse_catalog.py     # aggregate catalogs across installed addons
      get_detail.py         # meta + episode list for one item
      resolve_stream.py     # streams -> (optional debrid) -> playable direct URL
    infrastructure/         # adapters implementing ports
      addons/client.py      # httpx: manifest/catalog/meta/stream
      addons/repository.py  # installed-addon store + aggregation
      debrid/               # (later) real_debrid.py, alldebrid.py, torbox.py ...
      player/mpv_player.py  # libmpv wrapper
      cache/disk_cache.py   # posters + JSON on disk (platformdirs)
    presentation/           # Qt glue only
      controllers/          # QObject bridges: catalog, detail, player
      models/               # QAbstractListModel subclasses
      qml/                  # Main/Home/Detail/Player.qml + components/
    main.py                 # composition root: wire adapters into use cases
  tests/                    # pytest: domain + application fully unit-testable
```

**Rationale:** use cases are pure Python and tested with fake ports — no Qt,
no network in tests. Swapping addon-meta for TMDb later is a new adapter with
zero use-case change. Adding a debrid service is one new `DebridResolver`
adapter.

### Key ports (domain interfaces)

- `AddonSource` — `fetch_manifest`, `fetch_catalog`, `fetch_meta`, `fetch_streams`
- `DebridResolver` — `resolve(stream) -> direct_url` (later milestone)
- `MediaPlayer` — `play(url)`, `pause`, `seek`, `set_subtitle_track`, signals
- `Cache` — `get_or_fetch(url) -> bytes/path` for posters and JSON

## Stremio addon protocol (consumed endpoints)

- `GET {addon}/manifest.json` → id, version, name, resources, types, catalogs, idPrefixes
- `GET {addon}/catalog/{type}/{id}.json` (and `/{extra}.json`) → `{ metas: [...] }`
- `GET {addon}/meta/{type}/{id}.json` → `{ meta: {..., videos: [...] } }`
- `GET {addon}/stream/{type}/{id}.json` → `{ streams: [...] }`
- `GET {addon}/subtitles/{type}/{id}/{extra}.json` → `{ subtitles: [...] }` (as needed)

## Data flow (MVP loop)

1. **Add addon URL** → `InstallAddon` use case → `AddonClient.fetch_manifest()`
   → validate → `AddonRepository` stores it.
2. **Home** → `CatalogController` → `BrowseCatalog` → per addon
   `/catalog/{type}/{id}.json` → `list[MediaItem]` → `QAbstractListModel` →
   QML poster grid. Posters via `DiskCache.get_or_fetch` (async, off UI thread).
   **Superseded (2026-07-16):** QML's `Image` fetches through Qt's own network
   stack, which never reaches Python, so `DiskCache` could not actually serve
   posters and was never wired up. It has been removed in favour of
   `cache/network_cache.py`, which installs a `QNetworkDiskCache` on the QML
   engine — honouring CDN cache headers and bounded by size.
3. **Click poster** → `DetailController` → `GetDetail` →
   `/meta/{type}/{id}.json` → `MetaDetail` (+ `videos[]` for series).
4. **Play / episode** → `ResolveStream` → `/stream/{type}/{id}.json` →
   `list[Stream]`. MVP: streams carry direct `.url`; user picks. (Later:
   infoHash-only → `DebridResolver.resolve()` → direct URL.)
5. **Player** → `PlayerController.play(url)` → `MpvPlayer` (libmpv) renders into
   the QML surface. Controls overlay + subtitle-track selector.

**Threading:** all HTTP/IO is async (`httpx.AsyncClient`) on a background
asyncio loop; results marshalled to the Qt UI thread via signals. The UI never
blocks.

## MVP feature scope

**In:**
- Install addon by URL; bundle Cinemeta as a default so the app works on first run
- Movies + Series sections
- Category rows sourced from addon catalogs
- Poster grid with disk cache
- Detail page (poster, description, meta)
- Series episode list
- Stream-source picker
- libmpv player: play/pause/seek/volume + subtitle-track selector
- Direct-URL playback

**Out (later milestones, in build order):**
1. Client-side debrid resolvers (Real-Debrid, AllDebrid, TorBox, …)
2. Search
3. Continue watching
4. Trakt integration
5. 15+ color themes (Catppuccin, Nord, …)
6. Spoiler-blur for unwatched episodes
7. TMDb/TVDB metadata override (settings)
8. Settings + About pages
9. Animation polish

## Error handling

Ports raise typed domain errors (`AddonUnreachable`, `InvalidManifest`,
`NoStreams`, `PlaybackFailed`). Use cases let them propagate; controllers catch
them and expose an `error` signal → QML shows a non-blocking toast/inline
state. Catalog aggregation is per-addon fault-isolated: one failing addon is
logged and skipped, never breaking the grid. Player errors surface in the
player overlay.

## Testing

- **Domain + application:** pure `pytest` with fake ports (e.g. a fake
  `AddonSource` returning canned JSON) — full core-loop coverage, no network,
  no Qt.
- **Infrastructure:** addon client tested against recorded JSON fixtures.
- **Gates:** `mypy --strict` and `ruff` pass on every commit.
