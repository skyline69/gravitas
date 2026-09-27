# Gravitas

A minimal, no-nonsense alternative to Stremio that consumes Stremio addons. Built with Python, PySide6 (Qt6/QML), and libmpv.

## Features

- **Stremio addon support**: install any addon by manifest URL; catalogs, metadata, and posters come from the addon's own protocol (Cinemeta bundled as default).
- **Browse**: Movies and Series sections, catalog rows, poster grid with on-disk caching.
- **Playback**: embedded libmpv player (HEVC / MKV / MP4 / HLS …) with a reactive subtitle-track selector.
- **No torrent engine**: plays direct URLs only. Torrents are handled upstream, either by the addon itself or (planned) by debrid services (Real-Debrid, AllDebrid, TorBox, Premiumize, …).
- **Clean, memory-conscious architecture**: native Qt rendering, async I/O, far lighter than Electron-based clients.

## Current State


<img width="1392" height="932" alt="image" src="https://github.com/user-attachments/assets/b040651c-272e-47e1-8f58-09d8fd2a2faa" />

<img width="1392" height="932" alt="image" src="https://github.com/user-attachments/assets/e6c87f6d-3801-4b06-bc73-035a008e1411" />



## Install

Linux, macOS and Windows builds are published on every push to the `release` branch, as a single rolling release:

| Platform | Asset | Notes |
| --- | --- | --- |
| Linux | `Gravitas-*.AppImage`, `Gravitas.flatpak` | libmpv and yt-dlp bundled |
| macOS | `Gravitas.dmg` | ad-hoc signed; right-click → Open on first launch |
| Windows 10/11 (x64) | `Gravitas-Setup.exe` | per-user install, no admin needed; registers `stremio://` links |
| Windows 10/11 (x64) | `Gravitas-windows-x64.zip` | portable — unzip and run `gravitas.exe`; no `stremio://` handler |

The Windows binaries are not code-signed, so SmartScreen shows "Windows protected your PC" on first run: *More info* → *Run anyway*.

## Requirements

To run from source:

- Python ≥ 3.12
- [`uv`](https://docs.astral.sh/uv/)
- **libmpv** (system library, not a pip dependency):
  - Fedora `sudo dnf install mpv-libs`, Debian/Ubuntu `sudo apt install libmpv2`, macOS `brew install mpv`
  - Windows: no package ships it. Take `libmpv-2.dll` from an [mpv-dev build](https://github.com/shinchiro/mpv-winbuild-cmake/releases) and either put its folder on `%PATH%` or set `GRAVITAS_LIBMPV` to the DLL.
  - Without it the app browses fine but cannot play video.
- A graphical session (X11, Wayland, or a Windows desktop)
- Optional: `yt-dlp` on `PATH` for YouTube-backed streams and trailers (bundled in the released builds)

## Getting started

```bash
uv sync          # install dependencies into .venv
uv run gravitas  # launch
```

On first run, the Cinemeta addon is installed automatically and its catalogs populate the grid. Add more addons from the URL field on the home screen.

Where the app keeps its files:

| | Linux / macOS | Windows |
| --- | --- | --- |
| settings | `$XDG_CONFIG_HOME/gravitas` | `%APPDATA%\Gravitas` |
| progress + watchlist | `$XDG_DATA_HOME/gravitas` | `%LOCALAPPDATA%\Gravitas` |
| caches (artwork, addon JSON) | `$XDG_CACHE_HOME/gravitas` | `%LOCALAPPDATA%\Gravitas\Cache` |

## Packaging

```bash
uv run --with pyinstaller pyinstaller packaging/gravitas.spec --noconfirm
```

Produces `dist/Gravitas/` (Linux, Windows) or `dist/Gravitas.app` (macOS), with libmpv and yt-dlp inside. Then, per platform:

- Linux: `bash packaging/build_appimage.sh`, or `flatpak-builder` against `packaging/flatpak/dev.skyline.Gravitas.yaml`
- macOS: `create-dmg` (see `.github/workflows/release.yml`)
- Windows: `iscc packaging\windows\gravitas.iss` ([Inno Setup 6](https://jrsoftware.org/isdl.php)) → `dist\Gravitas-Setup.exe`

On Windows the spec downloads `libmpv-2.dll` and `yt-dlp.exe` itself (7-Zip or `py7zr` needed to unpack the mpv archive); `GRAVITAS_LIBMPV` skips the download.

## Development

```bash
uv run pytest -q              # run the test suite
uv run ruff check .           # lint
uv run ruff format --check .  # format check
uv run mypy src               # strict type check
```

Architecture and contributor guidance live in [`CLAUDE.md`](CLAUDE.md).
