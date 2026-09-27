"""Build the lean FFmpeg the native engine ships on Windows.

MSYS2's FFmpeg package enables every codec library it packages, and linked
against it the engine's module drags 104 DLLs (153 MiB) into the bundle:
x264, x265, SVT-AV1 and rav1e encoders, cairo, librsvg and GLib, GnuTLS,
libssh, librtmp, SRT, libbluray -- none of which a player of direct HTTP
streams calls. Their x264 and x265 also make that build GPL.

This builds the same FFmpeg release from source, inside MSYS2's UCRT64
environment, with what the engine uses and nothing else:

* every built-in decoder, demuxer, parser, bitstream filter and protocol --
  code inside FFmpeg's own DLLs, no dependency of their own;
* hardware decoding: D3D11VA, DXVA2, NVDEC (the CUDA driver is loaded at
  run time, so nothing is linked) and Vulkan;
* dav1d, because FFmpeg has no AV1 software decoder of its own; zlib; and
  Windows' own TLS (schannel) for FFmpeg's HTTP client, which the engine
  falls back to for hosts it cannot read in ranges. Not iconv: FFmpeg only
  uses it to re-encode subtitle character sets on request (sub_charenc),
  which the engine never makes.

No encoders, muxers, devices or filters, and no library that is not named
above (--disable-autodetect). The result is LGPL. build_native_player.py
links against it whenever it is there, and copies its DLLs instead of
MSYS2's. Same release as MSYS2's package, so the headers bindgen reads do
not change.

Run from any shell on Windows, after installing MSYS2 and, in it:

    pacman -S make diffutils mingw-w64-ucrt-x86_64-{gcc,nasm,pkgconf,dav1d} \\
        mingw-w64-ucrt-x86_64-{ffnvcodec-headers,vulkan-headers,zlib}

    uv run python scripts/build_ffmpeg_windows.py

The result goes to build/ffmpeg-windows/ (gitignored); a second run with
nothing changed does nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION = "9.0.2"
SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{VERSION}.tar.xz"
# Of the tarball above, whose signature (ffmpeg-{VERSION}.tar.xz.asc) checked
# out against FFmpeg's release key, FCF9 86EA 15E6 E293 A564 4F10 B432 2F04
# D676 58D8, when it was pinned.
SOURCE_SHA256 = "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"

BUILD = REPO / "build"
PREFIX = BUILD / "ffmpeg-windows"
MSYS2_ROOT = Path(os.environ.get("MSYS2_ROOT", "C:/msys64"))

CONFIGURE = [
    "--enable-shared",
    "--disable-static",
    "--disable-programs",
    "--disable-doc",
    "--disable-debug",
    # What the engine never calls.
    "--disable-avdevice",
    "--disable-avfilter",
    "--disable-encoders",
    "--disable-muxers",
    "--disable-devices",
    # Nothing is linked because the build machine happens to have it.
    "--disable-autodetect",
    "--enable-w32threads",
    "--enable-zlib",
    "--enable-libdav1d",
    "--enable-schannel",
    "--enable-d3d11va",
    "--enable-dxva2",
    "--enable-ffnvcodec",
    "--enable-nvdec",
    "--enable-vulkan",
]


def fetch_source(destination: Path) -> Path:
    """The release tarball, downloaded once and checked against the pin."""
    tarball = destination / f"ffmpeg-{VERSION}.tar.xz"
    if not tarball.is_file():
        print(f"downloading {SOURCE_URL}")
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            tarball.write_bytes(response.read())
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        tarball.unlink()
        raise SystemExit(f"error: {tarball.name} has SHA-256 {digest}, expected {SOURCE_SHA256}")
    return tarball


def msys2_bash(script: str, cwd: Path) -> None:
    """Runs `script` in MSYS2's UCRT64 environment, in `cwd`."""
    bash = MSYS2_ROOT / "usr" / "bin" / "bash.exe"
    if not bash.is_file():
        raise SystemExit(f"error: no MSYS2 at {MSYS2_ROOT} (MSYS2_ROOT moves it)")
    env = dict(os.environ, MSYSTEM="UCRT64", CHERE_INVOKING="1")
    subprocess.run([str(bash), "-lc", script], cwd=cwd, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="rebuild even if up to date")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("error: this builds the Windows FFmpeg, on Windows", file=sys.stderr)
        return 1

    # The build is described by its version and flags; the same description
    # means the same build.
    stamp = PREFIX / "gravitas-build.json"
    wanted = {"version": VERSION, "configure": CONFIGURE}
    if not args.force and stamp.is_file() and json.loads(stamp.read_text()) == wanted:
        print(f"{PREFIX.relative_to(REPO)} is up to date (FFmpeg {VERSION})")
        return 0

    BUILD.mkdir(exist_ok=True)
    tarball = fetch_source(BUILD)
    source = BUILD / f"ffmpeg-{VERSION}"
    shutil.rmtree(source, ignore_errors=True)
    with tarfile.open(tarball) as archive:
        archive.extractall(BUILD, filter="data")
    shutil.rmtree(PREFIX, ignore_errors=True)

    # A Windows path with forward slashes: MSYS2's shell and gcc both take
    # it, and it is what lands in the .pc files, which MinGW's pkg-config (a
    # Windows program) has to be able to read.
    configure = " ".join(["./configure", f"--prefix={PREFIX.as_posix()}", *CONFIGURE])
    jobs = os.cpu_count() or 2
    msys2_bash(f"{configure} && make -j{jobs} && make install", cwd=source)
    shutil.rmtree(source, ignore_errors=True)

    stamp.write_text(json.dumps(wanted, indent=2))
    dlls = sorted((PREFIX / "bin").glob("*.dll"))
    size = sum(dll.stat().st_size for dll in dlls) / 2**20
    where = PREFIX.relative_to(REPO)
    print(f"built FFmpeg {VERSION} into {where}: {len(dlls)} DLLs, {size:.0f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
