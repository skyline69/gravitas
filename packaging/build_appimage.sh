#!/usr/bin/env bash
# Wrap the PyInstaller one-dir build (dist/Gravitas) into an AppImage.
# Run packaging/gravitas.spec first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist/Gravitas"
APPDIR="$ROOT/dist/AppDir"
ARCH="$(uname -m)"

[ -d "$DIST" ] || { echo "dist/Gravitas missing — run pyinstaller first" >&2; exit 1; }

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr"
cp -a "$DIST" "$APPDIR/usr/app"

# One desktop entry + metainfo, shared with the Flatpak (packaging/flatpak/)
# so store metadata never drifts between the two builds. MimeType + `%u`
# register Gravitas as the stremio:// handler once the AppImage is integrated
# (appimaged, Gear Lever, or copying the desktop file to
# ~/.local/share/applications and running update-desktop-database) — an
# un-integrated AppImage registers nothing.
APP_ID="dev.skyline.Gravitas"
cp "$ROOT/packaging/flatpak/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
cp "$ROOT/packaging/gravitas.png" "$APPDIR/$APP_ID.png"
# Store-facing metadata: appimagetool embeds usr/share/metainfo, and tools
# like AppImageHub/Gear Lever read it for name/author/license. The desktop
# file is ALSO installed under usr/share/applications — appstream tooling
# resolves the metainfo's <launchable> against that path, not the top level.
install -Dm644 "$ROOT/packaging/flatpak/$APP_ID.metainfo.xml" \
    "$APPDIR/usr/share/metainfo/$APP_ID.appdata.xml"
install -Dm644 "$ROOT/packaging/flatpak/$APP_ID.desktop" \
    "$APPDIR/usr/share/applications/$APP_ID.desktop"
install -Dm644 "$ROOT/packaging/gravitas.png" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/app/gravitas" "$@"
EOF
chmod +x "$APPDIR/AppRun"

TOOL="$ROOT/dist/appimagetool"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "$TOOL"
fi

# appimagetool reads $ARCH from the environment to pick the target arch.
# APPIMAGE_EXTRACT_AND_RUN lets it (itself an AppImage) run where FUSE is
# unavailable — CI containers, sandboxes — instead of erroring on mount.
export ARCH APPIMAGE_EXTRACT_AND_RUN=1
# --no-appstream: appimagetool's appstreamcli check fetches every <url> in the
# metainfo and fails on warnings — with the repo private those URLs 404 and
# abort the build. The metainfo is validated structurally in-repo instead:
#   appstreamcli validate --no-net packaging/flatpak/dev.skyline.Gravitas.metainfo.xml
"$TOOL" --no-appstream "$APPDIR" "$ROOT/dist/Gravitas-${ARCH}.AppImage"
echo "built dist/Gravitas-${ARCH}.AppImage"
