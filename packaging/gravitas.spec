# PyInstaller spec: one-dir bundle with libmpv included.
# Build:  uv run --with pyinstaller pyinstaller packaging/gravitas.spec --noconfirm
# Output: dist/Gravitas/ (Linux), dist/Gravitas.app (macOS)

import ctypes.util
import os
import platform
import re
import stat
import subprocess
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


def precompile_qml() -> list[tuple[str, str]]:
    """Compile every .qml to bytecode and ship the .qmlc beside its source.

    Qt caches compiled QML per user, keyed by a hash of the file's PATH. An
    AppImage mounts itself at a fresh /tmp/.mount_GravitXXXXXX on every launch,
    so that key never repeats: measured here, three launches compiled all 39
    units three times and left 117 files behind in ~/.cache/Gravitas, growing
    without bound. A .qmlc next to the .qml is found by path, not by hash, so
    it survives the moving mount point — engine.load(Main.qml) drops from
    ~365ms to ~273ms, and nothing accumulates in the user's cache.

    Compiled by the PySide6 wheel's own qmlcachegen, so the bytecode matches
    the Qt that will read it; a mismatch is not fatal, Qt just recompiles.
    """
    import PySide6

    tool = Path(PySide6.__file__).parent / "Qt" / "libexec" / "qmlcachegen"
    qml_dir = SRC / "gravitas" / "presentation" / "qml"
    if not tool.exists():
        print(f"qmlcachegen not found at {tool} — shipping QML uncompiled")
        return []
    out_root = ROOT / "packaging" / ".cache" / "qmlc"
    compiled: list[tuple[str, str]] = []
    for source in sorted(qml_dir.rglob("*.qml")):
        relative = source.relative_to(qml_dir)
        target = out_root / relative.with_suffix(".qmlc")
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(tool), "--only-bytecode", "-I", str(qml_dir), "-o", str(target), str(source)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Not fatal: without the .qmlc Qt compiles that file at startup,
            # exactly as it does today.
            print(f"qmlcachegen failed for {relative}: {result.stderr.strip()}")
            continue
        destination = Path("gravitas/presentation/qml") / relative.parent
        compiled.append((str(target), str(destination)))
    print(f"precompiled {len(compiled)} QML files to bytecode")
    return compiled


libmpv = find_libmpv()
if libmpv is None:
    raise SystemExit("libmpv not found — install mpv (dnf/apt/brew) before building")

binaries = [(libmpv, ".")]
ytdlp = fetch_ytdlp()
if ytdlp is not None:
    binaries.append((ytdlp, "."))

# PySide6 ships every Qt module in one wheel and PyInstaller's hook collects
# the lot. Gravitas imports exactly seven of them (QtCore, QtGui, QtNetwork,
# QtOpenGL, QtQml, QtQuick, QtQuickControls2) plus QtWidgets, which qasync
# needs for QApplication; the QML side adds only QtQuick.Controls,
# QtQuick.Layouts, QtQuick.Window and Qt5Compat.GraphicalEffects. Naming the
# rest here keeps the analyser from following them at all.
PYSIDE_UNUSED = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQuick3D",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtHelp",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects",
    "PySide6.QtTextToSpeech",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=[
        (str(SRC / "gravitas" / "presentation" / "qml"), "gravitas/presentation/qml"),
        *precompile_qml(),
    ],
    # Lazy imports PyInstaller's scanner can miss.
    hiddenimports=["mpv", "qasync", "gravitas.presentation.video.mpv_item"],
    excludes=PYSIDE_UNUSED,
    # -OO: strip asserts and docstrings from every bundled module. Worth ~5ms
    # of import time (noise) but a real cut in bundle size and resident
    # memory, since PySide6's docstrings are large. The codebase's one assert
    # is a type-narrowing guard, not a runtime check.
    optimize=2,
)

# `excludes` above stops the ANALYSER; the PySide6 hook still copies Qt's own
# lib/qml/plugin trees wholesale, so the payload has to be filtered too.
# QtWebEngineCore alone is 194 MB — a whole Chromium — in an app with no web
# view. Everything named here is unreachable: no Python import, no QML import,
# no plugin we load. Translations go as well (the UI is English-only), as do
# the QML tooling plugins, which only serve the remote debugger.
QT_UNUSED = re.compile(
    r"""(?x)
    libQt6(WebEngine\w* | WebChannel\w* | WebSockets | WebView\w* | Pdf\w*
        | Quick3D\w* | Graphs\w* | Charts\w* | DataVisualization\w*
        | Multimedia\w* | SpatialAudio | Sensors\w* | Positioning\w* | Location
        | Bluetooth | Nfc | SerialPort | SerialBus | Sql | Test | QuickTest
        | Designer\w* | UiTools | Help | Scxml | StateMachine | RemoteObjects\w*
        | TextToSpeech | VirtualKeyboard\w* | QmlCompiler | QmlLS
        | QmlLocalStorage | StateMachineQml | ScxmlQml | 3D\w*)\.so
    | PySide6/Qt/qml/(QtQuick3D | Qt3D | QtGraphs | QtCharts | QtDataVisualization
        | QtWebEngine | QtWebChannel | QtWebSockets | QtWebView | QtTest
        | QtPositioning | QtLocation | QtMultimedia | QtSensors | QtScxml
        | QtRemoteObjects | QtTextToSpeech | QtVirtualKeyboard)/
    # Sub-modules of QtQuick itself whose backing library went with the list
    # above — a QML plugin without its library is a load error waiting to
    # happen, and LocalStorage/Pdf/VirtualKeyboard are nothing this UI imports.
    | PySide6/Qt/qml/QtQuick/(VirtualKeyboard | Pdf | LocalStorage
        | Scene2D | Scene3D)/
    | PySide6/Qt/qml/QtQml/StateMachine/
    | PySide6/Qt/(translations|resources|libexec)/
    | PySide6/Qt/plugins/(qmltooling | designer | sqldrivers | multimedia
        | webview | position | geoservices | sceneparsers | renderers
        | texttospeech | virtualkeyboard)/
    # Image formats: posters are JPEG/PNG/WebP. The PDF reader needs QtPdf,
    # and the TIFF one arrives without its libtiff already — both are dead
    # weight that only shows up as a failed plugin load.
    | PySide6/Qt/plugins/imageformats/libq(pdf|tiff)\.so
    | PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin\.so
    """
)


def _strip_unused(entries: list, kind: str) -> list:
    kept, dropped = [], 0
    for entry in entries:
        name = entry[0]
        if QT_UNUSED.search(name.replace(os.sep, "/")):
            dropped += Path(entry[1]).stat().st_size if Path(entry[1]).is_file() else 0
        else:
            kept.append(entry)
    print(f"dropped {len(entries) - len(kept)} unused Qt {kind} ({dropped / 1048576:.0f} MB)")
    return kept


a.binaries = _strip_unused(a.binaries, "binaries")
a.datas = _strip_unused(a.datas, "data files")

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
