# PyInstaller spec: one-dir bundle with libmpv included.
# Build:  uv run --with pyinstaller pyinstaller packaging/gravitas.spec --noconfirm
# Output: dist/Gravitas/ (Linux, Windows), dist/Gravitas.app (macOS)

import ctypes.util
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
SRC = ROOT / "src"
WINDOWS = sys.platform == "win32"
CACHE = ROOT / "packaging" / ".cache"

# Windows has no package manager that ships libmpv, so the build fetches it:
# shinchiro's mpv-dev packages are the de-facto official Windows libmpv, and
# libmpv-2.dll there is statically linked (ffmpeg, libass and the rest live
# inside it), so one file is the whole player.
_MPV_WINBUILD_RELEASE = (
    "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
)
# `mpv-dev-x86_64-<date>-git-<hash>.7z` and nothing else: the `-v3-` variant of
# the same name is compiled for x86-64-v3 (AVX2) and crashes on older CPUs.
_MPV_DEV_ASSET = re.compile(r"^mpv-dev-x86_64-\d{8}-git-[0-9a-f]+\.7z$")


def fetch_ytdlp() -> str | None:
    """Download the self-contained yt-dlp for the build host and cache it under
    packaging/.cache. Bundled under the exact name mpv's ytdl_hook spawns
    (`yt-dlp`, `yt-dlp.exe` on Windows) so YouTube/trailer streams resolve with
    no system yt-dlp. Set GRAVITAS_NO_YTDLP=1 to skip (e.g. Flatpak, which
    builds it as its own module)."""
    if os.environ.get("GRAVITAS_NO_YTDLP"):
        return None
    machine = platform.machine().lower()
    if WINDOWS:
        asset = "yt-dlp.exe"
    elif sys.platform == "darwin":
        asset = "yt-dlp_macos"
    elif machine in ("aarch64", "arm64"):
        asset = "yt-dlp_linux_aarch64"
    else:
        asset = "yt-dlp_linux"
    CACHE.mkdir(exist_ok=True)
    dest = CACHE / ("yt-dlp.exe" if WINDOWS else "yt-dlp")
    if not dest.exists():
        url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"
        print(f"fetching {url}")
        with urllib.request.urlopen(url) as r, open(dest, "wb") as f:  # noqa: S310
            f.write(r.read())
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(dest)


def _extract_7z(archive: Path, member: str, dest: Path) -> bool:
    """Pull one file out of a .7z. Tries the 7z CLI, then py7zr."""
    for exe in ("7z", "7za", "7zz"):
        found = shutil.which(exe)
        if found is None:
            continue
        # `e` extracts flat (no directory structure), -y answers every prompt.
        result = subprocess.run(
            [found, "e", str(archive), f"-o{dest.parent}", member, "-y"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and dest.exists():
            return True
        print(f"{exe} failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        import py7zr
    except ImportError:
        return False
    with py7zr.SevenZipFile(archive, "r") as archive_file:
        archive_file.extract(path=dest.parent, targets=[member])
    # py7zr keeps the archive's directory structure; flatten it.
    extracted = dest.parent / member
    if extracted != dest and extracted.exists():
        shutil.move(str(extracted), str(dest))
    return dest.exists()


def fetch_libmpv_windows() -> str | None:
    """Download libmpv-2.dll and cache it under packaging/.cache.

    Skipped entirely when GRAVITAS_LIBMPV points at a DLL already. Needs either
    the 7z CLI (present on GitHub's windows runners and with any 7-Zip install)
    or py7zr importable, because that is the only format these builds ship in.
    """
    CACHE.mkdir(exist_ok=True)
    dest = CACHE / "libmpv-2.dll"
    if dest.exists():
        return str(dest)
    print(f"fetching the libmpv release list from {_MPV_WINBUILD_RELEASE}")
    headers = {"Accept": "application/vnd.github+json"}
    # Unauthenticated GitHub API calls are 60/hour per IP, and CI runners share
    # theirs. The token (when the workflow exports one) lifts that to 1000 --
    # but it is deliberately NOT sent with the asset download below: that
    # redirects to a storage host which rejects a request carrying two
    # authentication mechanisms.
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(_MPV_WINBUILD_RELEASE, headers=headers)
    with urllib.request.urlopen(request) as response:  # noqa: S310
        release = json.load(response)
    assets = [a for a in release.get("assets", ()) if _MPV_DEV_ASSET.match(a.get("name", ""))]
    if not assets:
        raise SystemExit(
            "no mpv-dev-x86_64 asset in the latest shinchiro/mpv-winbuild-cmake "
            "release; download one by hand and point GRAVITAS_LIBMPV at its "
            "libmpv-2.dll"
        )
    asset = assets[0]
    archive = CACHE / asset["name"]
    if not archive.exists():
        print(f"fetching {asset['browser_download_url']}")
        with urllib.request.urlopen(asset["browser_download_url"]) as r:  # noqa: S310
            archive.write_bytes(r.read())
    if not _extract_7z(archive, "libmpv-2.dll", dest):
        raise SystemExit(
            f"cannot extract libmpv-2.dll from {archive}: install 7-Zip (so `7z` "
            "is on PATH) or `pip install py7zr`, or point GRAVITAS_LIBMPV at a "
            "libmpv-2.dll you extracted yourself"
        )
    return str(dest)


def find_libmpv() -> str | None:
    """Locate the native libmpv to bundle; PyInstaller pulls its dependency
    closure (ffmpeg, libass, ...) automatically.

    GRAVITAS_LIBMPV overrides the search on every platform — the way to build
    against a libmpv the system search cannot see.
    """
    override = os.environ.get("GRAVITAS_LIBMPV", "").strip()
    if override:
        if not Path(override).exists():
            raise SystemExit(f"GRAVITAS_LIBMPV={override} does not exist")
        return str(Path(override).resolve())
    if WINDOWS:
        # A DLL already on PATH (a hand-placed copy) beats a download.
        for name in ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll"):
            found = ctypes.util.find_library(name)
            if found:
                return str(Path(found).resolve())
        return fetch_libmpv_windows()
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

    # Unix wheels put the Qt tools in Qt/libexec; Windows wheels drop them at
    # the package root as .exe.
    package = Path(PySide6.__file__).parent
    candidates = (
        package / "Qt" / "libexec" / "qmlcachegen",
        package / "qmlcachegen.exe",
        package / "Qt" / "bin" / "qmlcachegen.exe",
    )
    tool = next((c for c in candidates if c.exists()), None)
    qml_dir = SRC / "gravitas" / "presentation" / "qml"
    if tool is None:
        print(f"qmlcachegen not found under {package} — shipping QML uncompiled")
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
    raise SystemExit(
        "libmpv not found — install mpv (dnf/apt/brew) before building, or set "
        "GRAVITAS_LIBMPV to the library file"
    )

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
#
# Two wheel layouts to cover: Unix keeps Qt under PySide6/Qt/{lib,qml,plugins}
# with libQt6Foo.so names, Windows drops Qt6Foo.dll at the package root and the
# trees one level up (PySide6/qml, PySide6/plugins). Hence the optional `Qt/`
# segment and the `(lib)?` / `\.(so|dll)` alternatives — a Windows-blind filter
# here means shipping Chromium in the installer.
QT_UNUSED = re.compile(
    r"""(?x)
    (lib)?Qt6(WebEngine\w* | WebChannel\w* | WebSockets | WebView\w* | Pdf\w*
        | Quick3D\w* | Graphs\w* | Charts\w* | DataVisualization\w*
        | Multimedia\w* | SpatialAudio | Sensors\w* | Positioning\w* | Location
        | Bluetooth | Nfc | SerialPort | SerialBus | Sql | Test | QuickTest
        | Designer\w* | UiTools | Help | Scxml | StateMachine | RemoteObjects\w*
        | TextToSpeech | VirtualKeyboard\w* | QmlCompiler | QmlLS
        | QmlLocalStorage | StateMachineQml | ScxmlQml | 3D\w*)\.(so|dll)
    # The out-of-process renderer that comes with QtWebEngineCore on Windows.
    | QtWebEngineProcess\.exe
    | PySide6/(Qt/)?qml/(QtQuick3D | Qt3D | QtGraphs | QtCharts | QtDataVisualization
        | QtWebEngine | QtWebChannel | QtWebSockets | QtWebView | QtTest
        | QtPositioning | QtLocation | QtMultimedia | QtSensors | QtScxml
        | QtRemoteObjects | QtTextToSpeech | QtVirtualKeyboard)/
    # Sub-modules of QtQuick itself whose backing library went with the list
    # above — a QML plugin without its library is a load error waiting to
    # happen, and LocalStorage/Pdf/VirtualKeyboard are nothing this UI imports.
    | PySide6/(Qt/)?qml/QtQuick/(VirtualKeyboard | Pdf | LocalStorage
        | Scene2D | Scene3D)/
    | PySide6/(Qt/)?qml/QtQml/StateMachine/
    # resources/ is WebEngine's icudtl.dat and .pak blobs; libexec its helper
    # binaries. Translations go because the UI is English-only.
    | PySide6/(Qt/)?(translations|resources|libexec)/
    | PySide6/(Qt/)?plugins/(qmltooling | designer | sqldrivers | multimedia
        | webview | position | geoservices | sceneparsers | renderers
        | texttospeech | virtualkeyboard)/
    # Image formats: posters are JPEG/PNG/WebP. The PDF reader needs QtPdf,
    # and the TIFF one arrives without its libtiff already — both are dead
    # weight that only shows up as a failed plugin load.
    | PySide6/(Qt/)?plugins/imageformats/(lib)?q(pdf|tiff)\.(so|dll)
    | PySide6/(Qt/)?plugins/platforminputcontexts/(lib)?qtvirtualkeyboardplugin\.(so|dll)
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

# Driver loaders move off the library path into _internal/fallback/ (Linux
# only: nothing here has a Windows or macOS counterpart, and libmpv-2.dll is
# statically linked anyway).
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
if sys.platform.startswith("linux"):
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
    # Windows only (both are ignored elsewhere): the .ico is what Explorer, the
    # taskbar and the installer show, and the version resource is what the file
    # properties dialog reads — an executable with neither looks like malware.
    icon=str(ROOT / "packaging" / "icon" / "gravitas.ico") if WINDOWS else None,
    version=str(ROOT / "packaging" / "windows" / "version_info.txt") if WINDOWS else None,
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
