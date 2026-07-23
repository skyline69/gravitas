# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Gravitas is a minimal, no-nonsense alternative to Stremio that consumes Stremio addons. Python + PySide6 (Qt6/QML) + libmpv.

## Commands

All commands run through `uv`.

```bash
uv sync                       # install deps + create .venv
uv run gravitas               # launch the app (needs a display + system libmpv)
uv run pytest -q              # full test suite
uv run pytest tests/presentation/test_player_controller.py::test_play_and_controls -v   # single test
uv run ruff check .           # lint gate
uv run ruff format --check .  # format gate (both ruff gates must pass)
uv run mypy src               # type gate: mypy --strict, on src only
```

The three quality gates (`ruff check`, `ruff format --check`, `mypy src`) are expected to pass on every commit.

**libmpv is a system dependency, not a pip package.** `python-mpv` only wraps the native `libmpv` and loads it at runtime. Without it installed (`dnf install mpv-libs` / `apt install libmpv2` / `brew install mpv`), the app launches and browses fine but playback raises `PlaybackFailed`. On macOS, Homebrew's lib dir isn't on the dyld fallback path — `mpv_player._ensure_libmpv_discoverable()` extends it in-process before `import mpv`. Windows has no libmpv package at all: drop `libmpv-2.dll` (from an [mpv-dev build](https://github.com/shinchiro/mpv-winbuild-cmake/releases)) on `%PATH%`, or point `GRAVITAS_LIBMPV` at it — the same variable the PyInstaller spec honours. Tests never require libmpv (see the factory seam below).

**Packaging targets: AppImage + Flatpak (Linux), dmg (macOS), Inno Setup installer + portable zip (Windows).** One spec, `packaging/gravitas.spec`, feeds all of them; `.github/workflows/release.yml` runs the four jobs into a single rolling release. The Windows build downloads `libmpv-2.dll` and `yt-dlp.exe` during the spec run (7-Zip or `py7zr` needed for the mpv archive), then `packaging/windows/gravitas.iss` wraps `dist/Gravitas/` into `dist/Gravitas-Setup.exe` and writes the `stremio://` handler into `HKCU\Software\Classes`. Regenerate `packaging/icon/gravitas.ico` with `uv run python scripts/build_ico.py` after editing the SVG.

## Architecture

Strict Clean Architecture. The dependency rule is enforced and must be preserved:

```
presentation  →  application  →  domain  ←  infrastructure
```

- **`domain/`** — pure. `models.py` (frozen dataclasses: `MediaItem`, `MetaDetail`, `Stream`, `AddonManifest`, …), `errors.py` (every error subclasses `GravitasError`), `ports.py` (`Protocol` interfaces: `AddonSource`, `MediaPlayer`, `ProgressStore`, `SettingsStore`, `ExternalIdResolver`, `DebridResolver`). Imports nothing from other layers, Qt, or httpx.
- **`application/`** — use cases (`InstallAddon`, `BrowseCatalog`, `GetDetail`, `ResolveStream`) plus `addon_repository.py` (installed-addon store + cross-addon catalog aggregation with per-addon fault isolation). Depends only on `domain`. **Do not import `infrastructure` here** — `AddonRepository` lives in `application` precisely to keep this rule.
- **`infrastructure/`** — adapters implementing domain ports: `addons/client.py` (httpx `AddonSource`), `addons/parsing.py` (pure Stremio-JSON→model functions, no I/O), `cache/ttl_cache.py` + `cache/network_cache.py`, `progress/sqlite_store.py`, `player/mpv_player.py`. May import `domain` + third-party, never `application`/`presentation`.
- **`presentation/`** — Qt/QML only. `controllers/` (QObject bridges exposing Slots/Signals), `models/` (`QAbstractListModel` subclasses feeding QML), `qml/`. Imports `domain`+`application`; nothing imports it back.
- **`main.py`** — the single composition root. The only place that references concrete adapter classes and wires them into use cases → controllers → QML context properties.

Data model follows the Stremio addon protocol: addons serve `/manifest.json`, `/catalog`, `/meta`, `/stream`. Metadata and posters come from the addon's own `/meta` (Cinemeta-style) — Cinemeta is bootstrapped as the default addon on startup.

**No torrent engine, ever.** The player only receives direct HTTP/HLS URLs. `ResolveStream` filters to `stream.is_direct` and raises `NoStreams` otherwise. Torrent→URL resolution belongs to a future client-side debrid milestone (a `DebridResolver` port stub exists but is unused).

**yt-dlp is an optional system dependency, like libmpv.** `Stream.playable_url` maps a protocol `ytId` (YouTube-backed streams and every `trailerStreams` entry) to a watch URL, which mpv resolves through its `ytdl_hook` — that hook shells out to `yt-dlp`. Without it on PATH, those streams raise `PlaybackFailed` like any other unplayable URL; everything else works untouched.

**Only `movie` and `series` exist here.** The protocol also defines `channel` and `tv`; supporting them would make `MediaType` four-way and turn Gravitas into an IPTV client. `parse_manifest` drops catalogs of those types and logs each one — an addon serving only them installs fine and shows nothing, so the log is the only clue. Deliberate; see the roadmap before "fixing" it.

## Critical gotchas (each cost a review cycle)

- **Async controller slots must use `@qasync.asyncSlot`, not `@Slot`.** PySide6 silently drops the coroutine from a plain `@Slot` async method — it's constructed and never awaited. `CatalogController.refresh` / `DetailController.load` rely on `asyncSlot` so they actually run on the qasync loop. The app runs asyncio on Qt's loop via `qasync`; all HTTP is async httpx.
- **Startup ordering is deterministic on purpose.** `Home.qml` does *not* self-refresh on load. `main.py`'s `bootstrap()` installs the default addon, binds its manifest, then drives the first catalog refresh — otherwise the grid refreshes against an empty repo and shows nothing.
- **libmpv is lazy-imported inside `_default_factory`** in `mpv_player.py` (not at module top), so the module and its tests import without libmpv present. `MpvPlayer` takes a `factory` seam — tests inject a `FakeMpv`, so player logic is unit-tested without the native lib. `locale.setlocale(LC_NUMERIC, "C")` must run at `mpv.MPV()` construction time (Qt resets the numeric locale), so it lives in the factory, not at import.
- **The player renders in-scene via the libmpv render API, not `wid` embedding** — `wid` is dead on Wayland. `MpvVideoItem` (a `QQuickFramebufferObject` in `presentation/video/`) pulls the opaque mpv handle through the `MediaPlayer.render_handle()` port method; the mpv render context must be created on the Qt render thread (inside `createFramebufferObject`), and its `update_cb` fires on mpv's thread — hop to the GUI thread before calling `update()`.
- **The scene-graph backend is per-platform, and the video item follows it — see `infrastructure/graphics.py`.** Linux/Windows force the OpenGL RHI (the only one `MpvVideoItem`'s FBO can render into). **macOS defaults to Metal**, because Qt Quick refuses its threaded render loop on macOS with OpenGL — forcing `QSG_RENDER_LOOP=threaded` crashes in `-[NSOpenGLContext setView:]` on the render thread — so OpenGL there means the **basic** single-threaded loop, with animations, QML incubation, Python and texture uploads all on one thread; tab switches hitch where Linux/Windows stay smooth. libmpv's render API only speaks OpenGL, so on Metal `main.py` registers `MpvSwVideoItem` instead under the same QML name `MpvVideo`: it renders through libmpv's **software** render API into a CPU buffer (~3.6 ms/frame at 1080p, ~6.8 ms at 4K on an M2) and takes `hwdec=auto-copy`, since a GPU surface has nowhere to go on a CPU path. `GRAVITAS_GRAPHICS=opengl` restores the zero-copy player on macOS and is ignored elsewhere. Two gotchas inside the software item: python-mpv's render-param table stops at 16, so `mpv_sw_item.py` registers the software params (17–20) into `MpvRenderParam.TYPES` itself; and `block_for_target_time=False` is load-bearing — without it every render call blocks a whole frame interval (~29 ms measured) on Qt's render thread.
- **QML's `Image` never reaches Python.** Artwork is fetched by Qt's own network
  stack, so an httpx-based cache cannot see it. `cache/network_cache.py` installs a
  `QQmlNetworkAccessManagerFactory` (before any QML loads, and kept alive on the
  engine like a context property) so `QNetworkDiskCache` caches posters honouring
  CDN cache headers. Addon JSON is separate: `AddonClient` caches it in-process
  per kind — manifest for the session, meta 24h, catalog 15m, **streams never**
  (links expire; `_get_json` defaults to uncached so a new endpoint is safe).
- **QML context properties need an explicit keep-alive.** `setContextProperty` doesn't take ownership; `main.py` holds them on `engine._gravitas_refs` or they get GC'd to null.
- **Model role names shadow QML ids inside delegates.** QML injects every role as a bare context property in delegate scope, so a role named `detail` breaks any `detail.someFunction()` call in that delegate (the Detail page id resolves to the row's string). Before adding a role, grep the delegates that use the model for ids with the same name; `StreamListModel` exposes its detail text as `extra` for exactly this reason.
- **QML has no unit tests** — it's verified at launch. `tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen` for headless-safe Qt tests. `mypy --strict` covers `src` only; `python-mpv`/`qasync` are untyped, so precise `# type: ignore[...]` (with the specific code) is the norm, never blanket ignores.
- **On Windows python-mpv finds the DLL at *import* time, off `%PATH%`** (`mpv.py` scans for `mpv-2.dll`, `libmpv-2.dll`, `mpv-1.dll`) — there is no `ctypes.util.find_library` seam to patch as on Unix. So `_ensure_bundled_libmpv_findable()` prepends `sys._MEIPASS` to `PATH` instead of redirecting `find_library`, and it must run before `import mpv`. Same story for `yt-dlp`, which is bundled as `yt-dlp.exe` there.
- **Never hardcode `~/.local/share` & co. — use `infrastructure/paths.py`.** `config_dir()`/`data_dir()`/`cache_dir()` keep the XDG layout on Linux *and macOS* (moving macOS to `~/Library` would strand existing databases) and map to `%APPDATA%` / `%LOCALAPPDATA%` / `%LOCALAPPDATA%\Cache` on Windows. Both branches take `environ`/`platform` arguments so either can be tested from either host.
- **`QUrl(str)` is not `QUrl.fromLocalFile(str)`.** A Windows path parses as scheme `c:`, so `engine.load()` silently loads nothing. Anything handing a filesystem path to Qt must go through `fromLocalFile`.
- **Windows decodes video copy-back, not zero-copy.** The scene graph is pinned to OpenGL, which cannot import D3D11/DXVA2 surfaces, so `_default_factory` defaults `hwdec` to `auto-copy` there (`auto-safe` elsewhere) — otherwise mpv falls back to software decoding. `GRAVITAS_HWDEC` still overrides.
- **The spec's Qt payload filter must match both wheel layouts.** Unix keeps Qt under `PySide6/Qt/{lib,qml,plugins}` with `libQt6Foo.so`; Windows puts `Qt6Foo.dll` at the package root with the trees one level up. A `.so`-only regex ships a whole Chromium (QtWebEngine, ~200 MB) inside the Windows installer — the release job asserts it is gone.

## Docs

Design spec and the implementation plan live under `docs/superpowers/`. `project-idea.md` is the original brief and the roadmap of deferred features (debrid, search, Trakt, themes, continue-watching, spoiler-blur, TMDb/TVDB override).
