# PyInstaller spec: one-dir bundle with libmpv included.
# Build:  uv run --with pyinstaller pyinstaller packaging/gravitas.spec --noconfirm
# Output: dist/Gravitas/ (Linux), dist/Gravitas.app (macOS)

import ctypes.util
import os
import platform
import re
import stat
import sys
import urllib.request
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
SRC = ROOT / "src"


def fetch_ytdlp() -> str | None:
    """Download the self-contained yt-dlp for the build host and cache it under
    packaging/.cache. Bundled as `yt-dlp` (the exact name mpv's ytdl_hook spawns)
    so YouTube/trailer streams resolve with no system yt-dlp. Set
    GRAVITAS_NO_YTDLP=1 to skip (e.g. Flatpak, which builds it as its own module)."""
    if os.environ.get("GRAVITAS_NO_YTDLP"):
        return None
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        asset = "yt-dlp_macos"
    elif machine in ("aarch64", "arm64"):
        asset = "yt-dlp_linux_aarch64"
    else:
        asset = "yt-dlp_linux"
    cache = ROOT / "packaging" / ".cache"
    cache.mkdir(exist_ok=True)
    dest = cache / "yt-dlp"
    if not dest.exists():
        url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"
        print(f"fetching {url}")
        with urllib.request.urlopen(url) as r, open(dest, "wb") as f:  # noqa: S310
            f.write(r.read())
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(dest)


def find_libmpv() -> str | None:
    """Locate the native libmpv to bundle; PyInstaller pulls its dependency
    closure (ffmpeg, libass, ...) automatically."""
    if sys.platform == "darwin":
        for prefix in ("/opt/homebrew/lib", "/usr/local/lib"):
            candidate = Path(prefix) / "libmpv.dylib"
            if candidate.exists():
                return str(candidate.resolve())
        return None
    soname = ctypes.util.find_library("mpv")
    if soname is None:
        return None
    for libdir in (
        "/usr/lib64",
        "/usr/lib/x86_64-linux-gnu",
        "/usr/lib/aarch64-linux-gnu",
        "/usr/lib",
        "/usr/local/lib",
    ):
        candidate = Path(libdir) / soname
        if candidate.exists():
            return str(candidate.resolve())
    return None


libmpv = find_libmpv()
if libmpv is None:
    raise SystemExit("libmpv not found — install mpv (dnf/apt/brew) before building")

binaries = [(libmpv, ".")]
ytdlp = fetch_ytdlp()
if ytdlp is not None:
    binaries.append((ytdlp, "."))

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=[
        (str(SRC / "gravitas" / "presentation" / "qml"), "gravitas/presentation/qml"),
    ],
    # Lazy imports PyInstaller's scanner can miss.
    hiddenimports=["mpv", "qasync", "gravitas.presentation.video.mpv_item"],
)

# Driver loaders move off the library path into _internal/fallback/.
#
# Each of these is a thin loader that dlopens the real driver out of a
# directory compiled in when IT was built: Ubuntu's libgbm looks under
# /usr/lib/x86_64-linux-gnu/dri, libva the same. Bundled on the library path
# they beat the host's copies and then find nothing on a Fedora/Arch/SUSE box
# — the "MESA-LOADER: failed to open ... wrong ELF class: ELFCLASS32" spam is
# that search falling through to /usr/lib/dri, which on multilib Fedora holds
# the 32-bit drivers. The host's own copies are the only ones that match the
# host's kernel driver, so they must win.
#
# They are still shipped, just somewhere ld.so won't look: libmpv links libva,
# libvdpau and libgbm with DT_NEEDED, so a host lacking them can't load libmpv
# at all and playback dies. AppRun symlinks exactly the missing ones onto the
# path at launch — host drivers when the host has them, a working player when
# it doesn't. A one-dir build run directly (no AppRun) skips that step and
# needs the host to provide them.
FALLBACK_DIR = "fallback"
_HOST_DRIVERS = re.compile(
    r"^lib(gbm|drm|EGL|GL|GLX|GLdispatch|OpenGL|GLESv2|GLU|glut|glapi"
    r"|va|va-drm|va-x11|va-wayland|va-glx|vdpau)\.so"
)
_moved = []
_binaries = []
for _entry in a.binaries:
    _name, *_rest = _entry
    if _HOST_DRIVERS.match(Path(_name).name):
        _moved.append(Path(_name).name)
        _binaries.append((f"{FALLBACK_DIR}/{Path(_name).name}", *_rest))
    else:
        _binaries.append(_entry)
if _moved:
    print(f"host driver libs moved to {FALLBACK_DIR}/: {', '.join(sorted(_moved))}")
a.binaries = _binaries

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="gravitas",
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="Gravitas",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Gravitas.app",
        icon=str(ROOT / "packaging" / "icon" / "gravitas.icns"),
        bundle_identifier="dev.skyline.gravitas",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
            # Register as a stremio:// handler so addon sites' "Install" links
            # open here. macOS delivers these as a QFileOpenEvent to the running
            # app (never as an argv), which DeepLinkListener catches.
            "CFBundleURLTypes": [
                {
                    "CFBundleURLName": "dev.skyline.gravitas.stremio",
                    "CFBundleURLSchemes": ["stremio"],
                }
            ],
        },
    )
