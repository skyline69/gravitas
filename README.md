# Gravitas

A minimal, no-nonsense alternative to Stremio that consumes Stremio addons. Built with Python, PySide6 (Qt6/QML), and libmpv.

> **Status:** MVP. Add a Stremio addon by URL, browse its Movies/Series catalogs as a poster grid, open a detail page, pick a direct-URL source, and play it with an embedded libmpv player and subtitle-track selector.

## Features

- **Stremio addon support** — install any addon by manifest URL; catalogs, metadata, and posters come from the addon's own protocol (Cinemeta bundled as default).
- **Browse** — Movies and Series sections, catalog rows, poster grid with on-disk caching.
- **Playback** — embedded libmpv player (HEVC / MKV / MP4 / HLS …) with a reactive subtitle-track selector.
- **No torrent engine** — plays direct URLs only. Torrents are handled upstream, either by the addon itself or (planned) by debrid services (Real-Debrid, AllDebrid, TorBox, Premiumize, …).
- **Clean, memory-conscious architecture** — native Qt rendering, async I/O, far lighter than Electron-based clients.

## Current State

<img width="1392" height="932" alt="Demo 1" src="https://github.com/user-attachments/assets/98d557b0-35f3-4886-a3e9-0774f745781e" />

<img width="1392" height="932" alt="Demo 2" src="https://github.com/user-attachments/assets/0d93480a-9899-4dc5-b269-38c5770f6ae6" />

## Requirements

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/)
- **libmpv** (system package, not a pip dependency) — e.g. `sudo dnf install mpv-libs` (Fedora) or `sudo apt install libmpv2` (Debian/Ubuntu). Without it the app browses fine but cannot play video.
- A graphical session (X11 or Wayland)

## Getting started

```bash
uv sync          # install dependencies into .venv
uv run gravitas  # launch
```

On first run, the Cinemeta addon is installed automatically and its catalogs populate the grid. Add more addons from the URL field on the home screen.

## Development

```bash
uv run pytest -q              # run the test suite
uv run ruff check .           # lint
uv run ruff format --check .  # format check
uv run mypy src               # strict type check
```

Architecture and contributor guidance live in [`CLAUDE.md`](CLAUDE.md); the design spec and implementation plan are under [`docs/superpowers/`](docs/superpowers/).

## Roadmap

Planned, in rough order: client-side debrid resolvers, search, continue-watching, Trakt integration, 15+ color themes (Catppuccin, Nord, …), spoiler-blur for unwatched episodes, TMDb/TVDB metadata override, seek/volume player controls, and multi-addon detail routing.
