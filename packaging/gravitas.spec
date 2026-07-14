# PyInstaller spec: one-dir bundle with libmpv included.
# Build:  uv run --with pyinstaller pyinstaller packaging/gravitas.spec --noconfirm
# Output: dist/Gravitas/ (Linux), dist/Gravitas.app (macOS)

import ctypes.util
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
SRC = ROOT / "src"


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

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=[(libmpv, ".")],
    datas=[
        (str(SRC / "gravitas" / "presentation" / "qml"), "gravitas/presentation/qml"),
    ],
    # Lazy imports PyInstaller's scanner can miss.
    hiddenimports=["mpv", "qasync", "gravitas.presentation.video.mpv_item"],
)

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
        bundle_identifier="dev.skyline.gravitas",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
        },
    )
