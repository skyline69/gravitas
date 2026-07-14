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

cat > "$APPDIR/gravitas.desktop" <<'EOF'
[Desktop Entry]
Name=Gravitas
Comment=Memory-efficient, Linux-first media center
Exec=gravitas
Icon=gravitas
Type=Application
Categories=AudioVideo;Video;Player;
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
