#!/usr/bin/env bash
# Build and install the Flatpak from a locally built PyInstaller bundle.
#
# The manifest packages dist/Gravitas as-is, which makes ONE thing decide
# whether the result can start at all: the glibc the bundle was compiled
# against. Every shared library PyInstaller collects comes from the build
# host, and glibc symbol versions are a floor, not a preference — a bundle
# built on Fedora 44 (glibc 2.43) inside the 24.08 runtime (2.40) dies before
# main() with
#
#     libc.so.6: version `GLIBC_ABI_GNU2_TLS' not found
#
# which names a symbol, not the actual problem. So that comparison is made
# here, up front, against the runtime the manifest actually asks for.
#
# The two other things a working Vulkan Flatpak needs — the native bridge
# built before PyInstaller runs, and the bridge surviving into dist/ — are
# checked too, because their absence is silent: the app launches and quietly
# renders on OpenGL.
#
#   bash packaging/build_flatpak.sh                 # build bundle, then install
#   bash packaging/build_flatpak.sh --keep-dist     # reuse the existing dist/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/packaging/flatpak/dev.skyline.Gravitas.yaml"
DIST="$ROOT/dist/Gravitas"
BRIDGE="libgravitas_video_bridge_vk.so"

KEEP_DIST=0
for arg in "$@"; do
    case "$arg" in
        --keep-dist) KEEP_DIST=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

command -v flatpak-builder >/dev/null 2>&1 || {
    echo "error: flatpak-builder not found (dnf install flatpak-builder)" >&2
    exit 1
}

# The runtime is whatever the manifest asks for; reading it here keeps the two
# from drifting after a runtime bump.
RUNTIME_ID="$(sed -n 's/^runtime:[[:space:]]*//p' "$MANIFEST" | head -1)"
RUNTIME_VERSION="$(sed -n "s/^runtime-version:[[:space:]]*'\{0,1\}\([^']*\)'\{0,1\}/\1/p" "$MANIFEST" | head -1)"
RUNTIME_REF="$RUNTIME_ID//$RUNTIME_VERSION"

if [ "$KEEP_DIST" -eq 0 ]; then
    if [ ! -f "$ROOT/src/gravitas/presentation/video/$BRIDGE" ]; then
        echo "warning: $BRIDGE not built — the Flatpak will render video on OpenGL." >&2
        echo "         Build it first for the zero-copy Vulkan path:" >&2
        echo "           uv run python scripts/build_video_bridge.py --qt <Qt prefix>" >&2
    fi
    uv run --with pyinstaller pyinstaller "$ROOT/packaging/gravitas.spec" --noconfirm
fi

[ -d "$DIST" ] || { echo "error: $DIST missing — drop --keep-dist" >&2; exit 1; }

# The glibc the bundle demands, read off the bundle rather than off this host:
# a dist/ built elsewhere (a container, another machine) is a normal thing to
# package, and asking `ldd --version` would then answer about the wrong system.
needed_glibc() {
    local reader
    if command -v objdump >/dev/null 2>&1; then
        reader="objdump -p"
    elif command -v readelf >/dev/null 2>&1; then
        reader="readelf -V"
    else
        return 1
    fi
    find "$DIST" -type f -name '*.so*' 2>/dev/null \
        | while read -r lib; do $reader "$lib" 2>/dev/null; done \
        | grep -oE 'GLIBC_2\.[0-9]+' | sort -uV | tail -1 | cut -d_ -f2
}

# The runtime's own glibc, straight from the horse: libc.so.6 is executable and
# prints its version. Beats parsing a version string out of the runtime name,
# which carries a release date and not a glibc.
runtime_glibc() {
    flatpak run --command=/usr/lib/x86_64-linux-gnu/libc.so.6 "$RUNTIME_REF" --version 2>/dev/null \
        | sed -n 's/.*version \([0-9][0-9.]*\)\.$/\1/p' | head -1
}

NEEDED="$(needed_glibc || true)"
HAVE="$(runtime_glibc || true)"
if [ -n "$NEEDED" ] && [ -n "$HAVE" ]; then
    if [ "$(printf '%s\n%s\n' "$NEEDED" "$HAVE" | sort -V | tail -1)" != "$HAVE" ]; then
        cat >&2 <<EOF
error: this bundle cannot run inside $RUNTIME_REF.

  dist/Gravitas needs glibc $NEEDED, the runtime provides $HAVE.

The bundle inherits the build host's glibc, and nothing in the Flatpak can
lower it. Either:

  * install the CI-built Flatpak instead — it is built on an old-glibc runner
    and carries the Vulkan bridge:
      gh release download rolling --pattern Gravitas.flatpak
      flatpak install --user ./Gravitas.flatpak

  * or rebuild dist/ on a system whose glibc is <= $HAVE (a container works)
    and re-run this script with --keep-dist.
EOF
        exit 1
    fi
    echo "glibc check: bundle needs $NEEDED, runtime has $HAVE"
fi

if find "$DIST" -name "$BRIDGE" | grep -q .; then
    echo "Vulkan zero-copy video bridge bundled"
else
    echo "warning: no $BRIDGE in the bundle; Linux video stays on OpenGL" >&2
fi

# The spec leaves fontconfig and freetype to the runtime. A dist/ that still
# carries them (built before that, or elsewhere) makes the sandbox reject the
# runtime's font config and lose the host's fonts -- worth saying before
# packaging it rather than as Fontconfig errors on every launch.
if find "$DIST" \( -name 'libfontconfig.so*' -o -name 'libfreetype.so*' \) | grep -q .; then
    echo "warning: the bundle carries its own fontconfig/freetype; rebuild dist/ with" >&2
    echo "         the current spec, or the runtime's font config will be rejected" >&2
fi

flatpak-builder --user --install --force-clean \
    "$ROOT/build/flatpak" "$MANIFEST"
echo "installed — run with: flatpak run dev.skyline.Gravitas"
