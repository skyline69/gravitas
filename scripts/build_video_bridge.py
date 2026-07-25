"""Build the macOS zero-copy video bridge (native/macos/ -> a .dylib).

The bridge is ObjC++ that calls Qt's C++ API, so building it needs Qt headers
matching the Qt inside the installed PySide6 wheel EXACTLY -- QRhi, which is
how a Metal texture reaches the scene graph, carries no binary compatibility
guarantee between releases. The wheel ships frameworks but no headers, so the
headers come from a Qt SDK install:

    uvx --from aqtinstall aqt install-qt mac desktop 6.11.1 clang_64 \\
        --outputdir ~/Qt --archives qtbase qtdeclarative
    uv run python scripts/build_video_bridge.py --qt ~/Qt/6.11.1/macos

The dylib LINKS against the SDK's frameworks but RESOLVES at run time to the
ones already loaded from the wheel: both carry the same @rpath install names,
so dyld reuses the loaded copy rather than pulling in a second Qt. That is
also why the versions must match.

Without the dylib the app runs fine -- the video item falls back to libmpv's
software render path (see presentation/video/mpv_sw_item.py).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import qVersion

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "native" / "macos" / "gravitas_video_bridge.mm"
# Next to the Python that loads it, so a dev checkout and a PyInstaller bundle
# find it the same way.
OUTPUT = REPO / "src" / "gravitas" / "presentation" / "video" / "libgravitas_video_bridge.dylib"

# The Linux/Vulkan sibling (native/linux/ -> a .so). Same exact-Qt-match
# discipline as macOS: QRhi has no binary compatibility guarantee, so the
# headers must match the PySide6 wheel's Qt and the loader enforces it at run
# time. Without the .so, Linux video stays on the default OpenGL path.
LINUX_SOURCE = REPO / "native" / "linux" / "gravitas_video_bridge_vk.cpp"
LINUX_OUTPUT = (
    REPO / "src" / "gravitas" / "presentation" / "video" / "libgravitas_video_bridge_vk.so"
)

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qt",
        type=Path,
        required=True,
        help=(
            "Qt SDK prefix -- ~/Qt/6.11.1/macos on macOS, ~/Qt/6.11.1/gcc_64 on "
            "Linux (must match the PySide6 wheel's Qt)"
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
    print("error: the video bridge is macOS- and Linux-only", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
