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

# Graphics/driver libs must come from the HOST, never the build machine.
# They are thin loaders that dlopen the real driver out of a path compiled in
# at build time: Ubuntu's libgbm looks in /usr/lib/x86_64-linux-gnu/dri, libva
# in the same triplet dir. Bundled, they win over the host's via
# LD_LIBRARY_PATH and then find nothing on a Fedora/Arch/SUSE box — the
# "MESA-LOADER: failed to open ... wrong ELF class: ELFCLASS32" spam is that
# search falling through to /usr/lib/dri, which on multilib Fedora holds the
# 32-bit drivers. Result: no mesa GL and no VA-API hwdec on any distro whose
# driver dir differs from the builder's. Dropping them costs nothing — every
# desktop with a GPU has its own copy, matched to its own kernel driver.
_HOST_GRAPHICS = re.compile(
    r"^lib(gbm|drm|EGL|GL|GLX|GLdispatch|OpenGL|GLESv2|GLU|glut|glapi"
    r"|va|va-drm|va-x11|va-wayland|va-glx|vdpau)\.so"
)
_dropped = [name for name, *_ in a.binaries if _HOST_GRAPHICS.match(Path(name).name)]
if _dropped:
    print(f"excluding host graphics libs from the bundle: {', '.join(sorted(_dropped))}")
a.binaries = [
    entry for entry in a.binaries if not _HOST_GRAPHICS.match(Path(entry[0]).name)
]

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
