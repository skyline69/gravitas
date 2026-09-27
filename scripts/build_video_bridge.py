"""Build the macOS zero-copy video bridge (native/macos/ -> a .dylib).

The bridge is ObjC++ that calls Qt's C++ API, so building it needs Qt headers
matching the Qt inside the installed PySide6 wheel EXACTLY -- QRhi, which is
how a Metal texture reaches the scene graph, carries no binary compatibility
guarantee between releases. The wheel ships frameworks but no headers, so the
headers come from a Qt SDK install:

    uvx --from aqtinstall aqt install-qt mac desktop 6.11.2 clang_64 \\
        --outputdir ~/Qt --archives qtbase qtdeclarative
    uv run python scripts/build_video_bridge.py --qt ~/Qt/6.11.2/macos

The dylib LINKS against the SDK's frameworks but RESOLVES at run time to the
ones already loaded from the wheel: both carry the same @rpath install names,
so dyld reuses the loaded copy rather than pulling in a second Qt. That is
also why the versions must match.

Without the dylib the app runs fine -- the video item falls back to libmpv's
software render path (see presentation/video/mpv_sw_item.py).

On Windows only the native engine's side is built (mpv stays on OpenGL there
and needs no bridge), with MSVC -- Qt's C++ ABI is MSVC's -- from a Visual
Studio or Build Tools install, found through vswhere when `cl` is not on PATH:

    uvx --from git+https://github.com/miurahr/aqtinstall aqt install-qt ^
        windows desktop 6.11.2 win64_msvc2022_64 --outputdir C:\\Qt ^
        --archives qtbase qtdeclarative
    uv run python scripts/build_video_bridge.py --qt C:\\Qt\\6.11.2\\msvc2022_64

aqtinstall 3.3.0 cannot find Qt 6.11 for Windows, whose repository Qt now
splits per architecture; its development branch can, and release.yml pins a
commit of it.

Qt's Vulkan headers include <vulkan/vulkan.h>, which the Qt SDK does not ship:
they come from the Vulkan SDK (VULKAN_SDK) or MSYS2's vulkan-headers package.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import qVersion

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "native" / "macos" / "gravitas_video_bridge.mm"
# The native engine's Qt side (zero-copy video through Metal), built into the
# same library for the same reason as its Linux sibling below.
NATIVE_SOURCE = REPO / "native" / "macos" / "gravitas_native_metal.mm"
# Next to the Python that loads it, so a dev checkout and a PyInstaller bundle
# find it the same way.
OUTPUT = REPO / "src" / "gravitas" / "presentation" / "video" / "libgravitas_video_bridge.dylib"

# The Linux/Vulkan sibling (native/linux/ -> a .so). Same exact-Qt-match
# discipline as macOS: QRhi has no binary compatibility guarantee, so the
# headers must match the PySide6 wheel's Qt and the loader enforces it at run
# time. Without the .so, Linux video stays on the default OpenGL path.
LINUX_SOURCE = REPO / "native" / "linux" / "gravitas_video_bridge_vk.cpp"
# The native engine's Qt side (zero-copy video through libplacebo), built into
# the same library: it needs the same Qt headers and the same exact-version
# discipline, and the engine only ever runs where this library loads.
LINUX_NATIVE_SOURCE = REPO / "native" / "linux" / "gravitas_native_vk.cpp"
LINUX_OUTPUT = (
    REPO / "src" / "gravitas" / "presentation" / "video" / "libgravitas_video_bridge_vk.so"
)

# Windows: the native engine's side alone, in a library of its own.
WINDOWS_OUTPUT = REPO / "src" / "gravitas" / "presentation" / "video" / "gravitas_native_vk.dll"
# Where MSYS2 keeps the Vulkan headers when there is no Vulkan SDK.
MSYS2_INCLUDE = Path(os.environ.get("MSYS2_ROOT", "C:/msys64")) / "ucrt64" / "include"

FRAMEWORKS = (
    "QtCore",
    "QtGui",
    "QtQuick",
    "Foundation",
    "Metal",
    "IOSurface",
    "OpenGL",
    "CoreFoundation",
)


def pyside_qt_dir() -> Path:
    """The Qt shipped inside the installed PySide6 wheel."""
    import PySide6

    return Path(PySide6.__file__).parent / "Qt"


def build(qt_prefix: Path, output: Path) -> int:
    qt_libs = qt_prefix / "lib"
    if not qt_libs.is_dir():
        print(f"error: no Qt frameworks under {qt_libs}", file=sys.stderr)
        return 1
    # The rhi headers are versioned one level deeper than the rest, and they
    # include each other as <rhi/...>, so both directories are needed.
    rhi_root = qt_libs / "QtGui.framework" / "Versions" / "A" / "Headers" / qVersion()
    if not (rhi_root / "QtGui" / "rhi" / "qrhi.h").is_file():
        print(
            f"error: no QRhi headers for Qt {qVersion()} under {rhi_root}.\n"
            f"       The Qt SDK must be the same version as the PySide6 wheel.",
            file=sys.stderr,
        )
        return 1

    command = [
        "xcrun",
        "clang++",
        "-std=c++20",
        "-fobjc-arc",
        "-dynamiclib",
        "-O2",
        "-fvisibility=hidden",  # only the extern "C" entry points are exported
        "-DGL_SILENCE_DEPRECATION",  # CGL is deprecated and has no replacement
        "-Wall",
        "-Wextra",
        "-o",
        str(output),
        str(SOURCE),
        str(NATIVE_SOURCE),
        "-F",
        str(qt_libs),
        "-I",
        str(qt_prefix / "include"),
        "-I",
        str(rhi_root),
        "-I",
        str(rhi_root / "QtGui"),
    ]
    for framework in FRAMEWORKS:
        command += ["-framework", framework]
    # Resolve Qt from the wheel at run time, wherever the wheel happens to live.
    command += ["-Wl,-rpath", f"-Wl,{pyside_qt_dir() / 'lib'}"]
    for arch in ("arm64", "x86_64"):
        command += ["-arch", arch]

    print(" ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        return result.returncode
    print(f"built {output}")
    return 0


def build_linux(qt_prefix: Path, output: Path) -> int:
    qt_libs = qt_prefix / "lib"
    if not qt_libs.is_dir():
        print(f"error: no Qt libraries under {qt_libs}", file=sys.stderr)
        return 1
    # The rhi headers are versioned one level deeper than the rest, and they
    # include each other as <rhi/...>, so both directories are needed.
    rhi_root = qt_prefix / "include" / "QtGui" / qVersion()
    if not (rhi_root / "QtGui" / "rhi" / "qrhi.h").is_file():
        print(
            f"error: no QRhi headers for Qt {qVersion()} under {rhi_root}.\n"
            f"       The Qt SDK must be the same version as the PySide6 wheel.",
            file=sys.stderr,
        )
        return 1

    command = [
        "g++",
        "-std=c++20",
        "-fPIC",
        "-shared",
        "-O2",
        "-fvisibility=hidden",  # only the extern "C" entry points are exported
        "-Wall",
        "-Wextra",
        "-o",
        str(output),
        str(LINUX_SOURCE),
        str(LINUX_NATIVE_SOURCE),
        "-I",
        str(qt_prefix / "include"),
        "-I",
        str(qt_prefix / "include" / "QtCore"),
        "-I",
        str(qt_prefix / "include" / "QtGui"),
        "-I",
        str(qt_prefix / "include" / "QtQuick"),
        "-I",
        str(rhi_root),
        "-I",
        str(rhi_root / "QtGui"),
        f"-L{qt_libs}",
        "-lQt6Core",
        "-lQt6Gui",
        "-lQt6Quick",
        "-lEGL",
        "-lGL",
        "-lvulkan",
        # Resolve Qt from the wheel at run time, wherever the wheel lives.
        f"-Wl,-rpath,{pyside_qt_dir() / 'lib'}",
    ]
    print(" ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        return result.returncode
    print(f"built {output}")
    return 0


def msvc_environment() -> dict[str, str]:
    """The environment `cl` and `link` need: this one when `cl` is already on
    PATH (a Developer prompt), else vcvars64.bat's, found through vswhere."""
    if shutil.which("cl"):
        return dict(os.environ)
    vswhere = (
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Microsoft Visual Studio"
        / "Installer"
        / "vswhere.exe"
    )
    if not vswhere.is_file():
        raise SystemExit("error: no MSVC -- install Visual Studio or its Build Tools (C++)")
    install = subprocess.run(
        [
            str(vswhere),
            "-latest",
            "-products",
            "*",
            "-requires",
            "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    vcvars = Path(install) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not install or not vcvars.is_file():
        raise SystemExit("error: no MSVC x64 tools in the Visual Studio install")
    dump = subprocess.run(
        f'"{vcvars}" >nul && set', shell=True, capture_output=True, text=True, check=True
    ).stdout
    # Upper-cased as os.environ does on Windows, where names are
    # case-insensitive: `set` prints `Path`, and PATH is what gets looked up.
    pairs = (line.split("=", 1) for line in dump.splitlines() if "=" in line)
    return {name.upper(): value for name, value in pairs}


def vulkan_headers(staging: Path) -> Path | None:
    """A directory holding <vulkan/vulkan.h> and nothing else a compiler might
    pick up. The Vulkan SDK's include directory is that already; MSYS2's is
    full of MinGW's C headers, which MSVC must not see, so the two Vulkan
    directories are copied out of it."""
    sdk = os.environ.get("VULKAN_SDK")
    if sdk and (Path(sdk) / "Include" / "vulkan" / "vulkan.h").is_file():
        return Path(sdk) / "Include"
    if not (MSYS2_INCLUDE / "vulkan" / "vulkan.h").is_file():
        return None
    for name in ("vulkan", "vk_video"):
        if (MSYS2_INCLUDE / name).is_dir():
            shutil.copytree(MSYS2_INCLUDE / name, staging / name)
    return staging


def build_windows(qt_prefix: Path, output: Path) -> int:
    qt_libs = qt_prefix / "lib"
    if not (qt_libs / "Qt6Quick.lib").is_file():
        print(f"error: no Qt import libraries under {qt_libs}", file=sys.stderr)
        return 1
    rhi_root = qt_prefix / "include" / "QtGui" / qVersion()
    if not (rhi_root / "QtGui" / "rhi" / "qrhi.h").is_file():
        print(
            f"error: no QRhi headers for Qt {qVersion()} under {rhi_root}.\n"
            f"       The Qt SDK must be the same version as the PySide6 wheel.",
            file=sys.stderr,
        )
        return 1
    env = msvc_environment()
    with tempfile.TemporaryDirectory() as scratch:
        vulkan = vulkan_headers(Path(scratch) / "include")
        if vulkan is None:
            print(
                "error: no Vulkan headers -- install the Vulkan SDK, or MSYS2's "
                "mingw-w64-ucrt-x86_64-vulkan-headers",
                file=sys.stderr,
            )
            return 1
        # Resolved against vcvars' PATH: Windows looks a program up on the
        # parent's, whatever the child is given.
        cl = shutil.which("cl", path=env.get("PATH")) or "cl"
        command = [
            cl,
            "/nologo",
            "/std:c++20",
            "/EHsc",
            "/O2",
            "/MD",  # the dynamic CRT, as Qt and Python use
            "/permissive-",
            "/Zc:__cplusplus",  # Qt refuses to build without it
            "/utf-8",
            "/W3",
            "/LD",
            str(LINUX_NATIVE_SOURCE),
            f"/Fe:{output}",
            f"/Fo:{scratch}\\",
        ]
        for include in (
            qt_prefix / "include",
            qt_prefix / "include" / "QtCore",
            qt_prefix / "include" / "QtGui",
            qt_prefix / "include" / "QtQuick",
            rhi_root,
            rhi_root / "QtGui",
            vulkan,
        ):
            command.append(f"/I{include}")
        # The import library and export file are build leftovers, not
        # something anything links against: kept out of the source tree.
        command += [
            "/link",
            f"/LIBPATH:{qt_libs}",
            f"/IMPLIB:{Path(scratch) / 'gravitas_native_vk.lib'}",
            "Qt6Core.lib",
            "Qt6Gui.lib",
            "Qt6Quick.lib",
        ]
        print(" ".join(command))
        result = subprocess.run(command, env=env, check=False)
    if result.returncode != 0:
        return result.returncode
    print(f"built {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qt",
        type=Path,
        required=True,
        help=(
            "Qt SDK prefix -- ~/Qt/6.11.2/macos on macOS, ~/Qt/6.11.2/gcc_64 on "
            "Linux, C:\\Qt\\6.11.2\\msvc2022_64 on Windows (must match the "
            "PySide6 wheel's Qt)"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="defaults to the platform's library next to the Python that loads it",
    )
    args = parser.parse_args()
    if sys.platform == "darwin":
        output = args.output or OUTPUT
        output.parent.mkdir(parents=True, exist_ok=True)
        return build(args.qt.expanduser(), output)
    if sys.platform.startswith("linux"):
        output = args.output or LINUX_OUTPUT
        output.parent.mkdir(parents=True, exist_ok=True)
        return build_linux(args.qt.expanduser(), output)
    if sys.platform == "win32":
        output = args.output or WINDOWS_OUTPUT
        output.parent.mkdir(parents=True, exist_ok=True)
        return build_windows(args.qt.expanduser(), output)
    print("error: the video bridge is built on macOS, Linux and Windows", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
