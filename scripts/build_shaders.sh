#!/bin/sh
# Bake the QML shaders to .qsb (all backends: GLSL for the OpenGL scene
# graph the app pins, plus HLSL/MSL so a backend change never breaks them).
# Re-run after editing any *.frag / *.vert under qml/shaders.
set -e
cd "$(dirname "$0")/../src/gravitas/presentation/qml/shaders"
for f in *.frag *.vert; do
    [ -e "$f" ] || continue
    uv run pyside6-qsb --glsl "100 es,120,150" --hlsl 50 --msl 12 -o "$f.qsb" "$f"
    echo "baked $f.qsb"
done
