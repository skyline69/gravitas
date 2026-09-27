"""Build the native player engine (native/player/ -> a Python extension).

The engine is a Rust workspace; its `gravitas-player-python` crate is a PyO3
module. This builds it with cargo and copies it next to the adapter that
imports it, so a dev checkout and a PyInstaller bundle find it the same way
the video bridges are found:

    uv run python scripts/build_native_player.py            # release build
    uv run python scripts/build_native_player.py --debug

It needs a Rust toolchain and FFmpeg's development files (headers and
pkg-config files for libavformat, libavcodec, libavutil, libswresample and
libswscale): `dnf install ffmpeg-devel` (RPM Fusion), `apt install
libavformat-dev libavcodec-dev libswresample-dev libswscale-dev`, or `brew
install ffmpeg`. Linux also needs ALSA's (`alsa-lib-devel` / `libasound2-dev`).

Windows builds with MSYS2's UCRT64 toolchain, which has every library the
engine needs as a package, found through pkg-config exactly as on Linux:

    pacman -S mingw-w64-ucrt-x86_64-{rust,pkgconf,clang,binutils,ffmpeg,libplacebo,libass}

The script puts C:\\msys64\\ucrt64\\bin on PATH itself, and copies the MinGW
DLLs the module links into gravitas_player.libs/ beside it (native_player.py
registers that directory before the import). MSYS2's FFmpeg brings ~80 DLLs
the engine never calls, and is GPL: when scripts/build_ffmpeg_windows.py has
built the lean one into build/ffmpeg-windows/, the module is built against
that instead, and its DLLs are the ones copied.

Without the module the app runs exactly as before: GRAVITAS_PLAYER=native
logs that it is missing and plays through mpv.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKSPACE = REPO / "native" / "player"
CRATE = "gravitas-player-python"
# The name the module is imported by; PyO3 exports PyInit_gravitas_player.
MODULE = "gravitas_player"
DESTINATION = REPO / "src" / "gravitas" / "infrastructure" / "player"
# Windows: MSYS2's UCRT64 environment, and where the module's DLLs go.
MSYS2_UCRT64 = Path(os.environ.get("MSYS2_ROOT", "C:/msys64")) / "ucrt64"
WINDOWS_LIBS = DESTINATION / f"{MODULE}.libs"
# The lean FFmpeg scripts/build_ffmpeg_windows.py builds, used when present.
LEAN_FFMPEG = REPO / "build" / "ffmpeg-windows"


def target_directory(cargo: str, env: dict[str, str]) -> Path:
    """Cargo's target directory for the workspace: `native/player/target`
    unless a cargo config or CARGO_TARGET_DIR moves it (a shared one under
    ~/.cache is common), so it is asked rather than assumed."""
    metadata = subprocess.run(
        [cargo, "metadata", "--format-version", "1", "--no-deps"],
        cwd=WORKSPACE,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(json.loads(metadata.stdout)["target_directory"])


def built_library(profile: str, cargo: str, env: dict[str, str]) -> Path:
    """Where cargo leaves the cdylib on this platform."""
    target = target_directory(cargo, env) / profile
    if sys.platform == "win32":
        return target / f"{MODULE}.dll"
    if sys.platform == "darwin":
        return target / f"lib{MODULE}.dylib"
    return target / f"lib{MODULE}.so"


def installed_name() -> str:
    """The file name Python imports an abi3 extension by."""
    return f"{MODULE}.pyd" if sys.platform == "win32" else f"{MODULE}.abi3.so"


def ucrt64_environment(env: dict[str, str]) -> dict[str, str]:
    """`env` with MSYS2's UCRT64 tools first on PATH: its cargo, the gcc that
    links, pkg-config, and libclang for bindgen."""
    bin_dir = MSYS2_UCRT64 / "bin"
    if not (bin_dir / "cargo.exe").is_file():
        raise SystemExit(
            f"error: no cargo in {bin_dir}; install MSYS2 and "
            "mingw-w64-ucrt-x86_64-{rust,pkgconf,clang,binutils,ffmpeg,libplacebo,libass}"
        )
    env = dict(env)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env.setdefault("LIBCLANG_PATH", str(bin_dir))
    if (LEAN_FFMPEG / "lib" / "pkgconfig").is_dir():
        # Found before MSYS2's by pkg-config, and first on PATH for the build
        # scripts that run; everything else still comes from MSYS2.
        pkgconfig = [str(LEAN_FFMPEG / "lib" / "pkgconfig"), env.get("PKG_CONFIG_PATH", "")]
        env["PKG_CONFIG_PATH"] = os.pathsep.join(p for p in pkgconfig if p)
        env["PATH"] = f"{LEAN_FFMPEG / 'bin'}{os.pathsep}{env['PATH']}"
        print(f"FFmpeg: the lean build in {LEAN_FFMPEG.relative_to(REPO)}")
    else:
        print(
            "FFmpeg: MSYS2's (every codec library, GPL); "
            "scripts/build_ffmpeg_windows.py builds the lean one"
        )
    return env


def imported_dlls(binary: Path, objdump: str, env: dict[str, str]) -> list[str]:
    """The DLL names `binary` imports, read from its import table."""
    dump = subprocess.run(
        [objdump, "-p", str(binary)], env=env, capture_output=True, text=True, check=True
    ).stdout
    return [
        line.split(":", 1)[1].strip()
        for line in dump.splitlines()
        if line.strip().startswith("DLL Name:")
    ]


def copy_windows_dependencies(module: Path, env: dict[str, str]) -> None:
    """Copies every DLL `module` loads from the lean FFmpeg or MSYS2 into
    WINDOWS_LIBS, which is rebuilt from scratch so a dependency dropped since
    the last build goes.

    The walk resolves each import itself, lean FFmpeg first: ntldd, which
    this used before, looks in its own directory -- MSYS2's bin, where
    MSYS2's FFmpeg lives under the very same names -- before PATH, so it
    always answered with the FFmpeg the module was not linked against. A
    name found in neither (a system DLL) is Windows' to load."""
    objdump = shutil.which("objdump", path=env.get("PATH")) or "objdump"
    search = [d for d in (LEAN_FFMPEG / "bin", MSYS2_UCRT64 / "bin") if d.is_dir()]
    wanted: dict[str, Path] = {}
    pending = [module]
    while pending:
        for name in imported_dlls(pending.pop(), objdump, env):
            if name.lower() in wanted:
                continue
            found = next((d / name for d in search if (d / name).is_file()), None)
            if found is not None:
                wanted[name.lower()] = found
                pending.append(found)
    shutil.rmtree(WINDOWS_LIBS, ignore_errors=True)
    WINDOWS_LIBS.mkdir(parents=True)
    for dll in sorted(wanted.values()):
        shutil.copy2(dll, WINDOWS_LIBS / dll.name)
    size = sum(f.stat().st_size for f in WINDOWS_LIBS.iterdir()) / 2**20
    print(f"copied {len(wanted)} DLLs ({size:.0f} MiB) into {WINDOWS_LIBS.relative_to(REPO)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--debug", action="store_true", help="build without optimisations")
    args = parser.parse_args()

    env = dict(os.environ)
    if sys.platform == "win32":
        env = ucrt64_environment(env)
    elif shutil.which("cargo") is None:
        print("cargo was not found; install Rust from https://rustup.rs", file=sys.stderr)
        return 1
    profile = "debug" if args.debug else "release"
    # Link as an extension module: interpreter symbols come from the process
    # that imports it (see the PyO3 dependency in the crate's Cargo.toml).
    env["PYO3_BUILD_EXTENSION_MODULE"] = "1"
    env.setdefault("PYO3_PYTHON", sys.executable)
    # Resolved against `env`'s PATH: Windows looks a program up on the
    # parent's, whatever the child is given.
    cargo = shutil.which("cargo", path=env.get("PATH")) or "cargo"
    command = [cargo, "build", "-p", CRATE]
    if not args.debug:
        command.append("--release")
    result = subprocess.run(command, cwd=WORKSPACE, env=env, check=False)
    if result.returncode != 0:
        return result.returncode

    source = built_library(profile, cargo, env)
    destination = DESTINATION / installed_name()
    shutil.copy2(source, destination)
    print(f"installed {destination.relative_to(REPO)}")
    if sys.platform == "win32":
        copy_windows_dependencies(destination, env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
