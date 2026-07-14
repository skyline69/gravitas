# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Gravitas is a memory-efficient, Linux-first desktop media center — a Stremio alternative that consumes Stremio addons. Python + PySide6 (Qt6/QML) + libmpv.

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

**libmpv is a system dependency, not a pip package.** `python-mpv` only wraps the native `libmpv` and loads it at runtime. Without it installed (`dnf install mpv-libs` / `apt install libmpv2` / `brew install mpv`), the app launches and browses fine but playback raises `PlaybackFailed`. On macOS, Homebrew's lib dir isn't on the dyld fallback path — `mpv_player._ensure_libmpv_discoverable()` extends it in-process before `import mpv`. Tests never require libmpv (see the factory seam below).

## Architecture

Strict Clean Architecture. The dependency rule is enforced and must be preserved:

```
presentation  →  application  →  domain  ←  infrastructure
```

- **`domain/`** — pure. `models.py` (frozen dataclasses: `MediaItem`, `MetaDetail`, `Stream`, `AddonManifest`, …), `errors.py` (every error subclasses `GravitasError`), `ports.py` (`Protocol` interfaces: `AddonSource`, `MediaPlayer`, `Cache`, `DebridResolver`). Imports nothing from other layers, Qt, or httpx.
- **`application/`** — use cases (`InstallAddon`, `BrowseCatalog`, `GetDetail`, `ResolveStream`) plus `addon_repository.py` (installed-addon store + cross-addon catalog aggregation with per-addon fault isolation). Depends only on `domain`. **Do not import `infrastructure` here** — `AddonRepository` lives in `application` precisely to keep this rule.
- **`infrastructure/`** — adapters implementing domain ports: `addons/client.py` (httpx `AddonSource`), `addons/parsing.py` (pure Stremio-JSON→model functions, no I/O), `cache/disk_cache.py`, `player/mpv_player.py`. May import `domain` + third-party, never `application`/`presentation`.
- **`presentation/`** — Qt/QML only. `controllers/` (QObject bridges exposing Slots/Signals), `models/` (`QAbstractListModel` subclasses feeding QML), `qml/`. Imports `domain`+`application`; nothing imports it back.
- **`main.py`** — the single composition root. The only place that references concrete adapter classes and wires them into use cases → controllers → QML context properties.

Data model follows the Stremio addon protocol: addons serve `/manifest.json`, `/catalog`, `/meta`, `/stream`. Metadata and posters come from the addon's own `/meta` (Cinemeta-style) — Cinemeta is bootstrapped as the default addon on startup.

**No torrent engine, ever.** The player only receives direct HTTP/HLS URLs. `ResolveStream` filters to `stream.is_direct` and raises `NoStreams` otherwise. Torrent→URL resolution belongs to a future client-side debrid milestone (a `DebridResolver` port stub exists but is unused).

## Critical gotchas (each cost a review cycle)

- **Async controller slots must use `@qasync.asyncSlot`, not `@Slot`.** PySide6 silently drops the coroutine from a plain `@Slot` async method — it's constructed and never awaited. `CatalogController.refresh` / `DetailController.load` rely on `asyncSlot` so they actually run on the qasync loop. The app runs asyncio on Qt's loop via `qasync`; all HTTP is async httpx.
- **Startup ordering is deterministic on purpose.** `Home.qml` does *not* self-refresh on load. `main.py`'s `bootstrap()` installs the default addon, binds its manifest, then drives the first catalog refresh — otherwise the grid refreshes against an empty repo and shows nothing.
- **libmpv is lazy-imported inside `_default_factory`** in `mpv_player.py` (not at module top), so the module and its tests import without libmpv present. `MpvPlayer` takes a `factory` seam — tests inject a `FakeMpv`, so player logic is unit-tested without the native lib. `locale.setlocale(LC_NUMERIC, "C")` must run at `mpv.MPV()` construction time (Qt resets the numeric locale), so it lives in the factory, not at import.
- **The player renders in-scene via the libmpv render API, not `wid` embedding** — `wid` is dead on Wayland. `MpvVideoItem` (a `QQuickFramebufferObject` in `presentation/video/`) pulls the opaque mpv handle through the `MediaPlayer.render_handle()` port method; the mpv render context must be created on the Qt render thread (inside `createFramebufferObject`), and its `update_cb` fires on mpv's thread — hop to the GUI thread before calling `update()`.
- **QML context properties need an explicit keep-alive.** `setContextProperty` doesn't take ownership; `main.py` holds them on `engine._gravitas_refs` or they get GC'd to null.
- **Model role names shadow QML ids inside delegates.** QML injects every role as a bare context property in delegate scope, so a role named `detail` breaks any `detail.someFunction()` call in that delegate (the Detail page id resolves to the row's string). Before adding a role, grep the delegates that use the model for ids with the same name; `StreamListModel` exposes its detail text as `extra` for exactly this reason.
- **QML has no unit tests** — it's verified at launch. `tests/conftest.py` forces `QT_QPA_PLATFORM=offscreen` for headless-safe Qt tests. `mypy --strict` covers `src` only; `python-mpv`/`qasync` are untyped, so precise `# type: ignore[...]` (with the specific code) is the norm, never blanket ignores.

## Docs

Design spec and the implementation plan live under `docs/superpowers/`. `project-idea.md` is the original brief and the roadmap of deferred features (debrid, search, Trakt, themes, continue-watching, spoiler-blur, TMDb/TVDB override).
