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

# MimeType + `%u` register Gravitas as the stremio:// handler, so an addon
# site's "Install" link opens here. The desktop entry only takes effect once
# the AppImage is integrated (appimaged, Gear Lever, or copying this file to
# ~/.local/share/applications and running update-desktop-database) — an
# un-integrated AppImage registers nothing.
cat > "$APPDIR/gravitas.desktop" <<'EOF'
[Desktop Entry]
Name=Gravitas
Comment=Memory-efficient, Linux-first media center
Exec=gravitas %u
Icon=gravitas
Type=Application
Categories=AudioVideo;Video;Player;
MimeType=x-scheme-handler/stremio;
EOF
cp "$ROOT/packaging/gravitas.png" "$APPDIR/gravitas.png"

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

ARCH="$ARCH" "$TOOL" "$APPDIR" "$ROOT/dist/Gravitas-${ARCH}.AppImage"
echo "built dist/Gravitas-${ARCH}.AppImage"
