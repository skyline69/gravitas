#version 440
// Ambient background: two huge, very slow glows drifting over the page
// colour. Deliberately faint — the ceiling is a few percent above Theme.bg,
// so posters and text keep full contrast. Compiled with
// scripts/build_shaders.sh; the .qsb next to this file is what QML loads.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float time;
    float aspect;
};

void main() {
    vec2 uv = vec2(qt_TexCoord0.x * aspect, qt_TexCoord0.y);

    // Orbit centres. Every angular rate is a multiple of 0.01, so a time
    // range of 200*pi loops each sin/cos through whole cycles — the
    // animation wraps with no visible jump.
    vec2 c1 = vec2(0.30 * aspect + 0.30 * sin(time * 0.05),
                   0.30 + 0.22 * cos(time * 0.03));
    vec2 c2 = vec2(0.72 * aspect + 0.26 * cos(time * 0.02),
                   0.74 + 0.24 * sin(time * 0.04));

    float g1 = smoothstep(1.05, 0.0, distance(uv, c1));
    float g2 = smoothstep(1.15, 0.0, distance(uv, c2));

    vec3 base = vec3(0.0784, 0.0784, 0.0784);   // Theme.bg #141414
    vec3 accent = vec3(0.545, 0.361, 0.965);    // Theme.accent #8B5CF6
    vec3 cool   = vec3(0.231, 0.510, 0.965);    // a quiet blue

    vec3 col = base;
    col += accent * g1 * g1 * 0.040;
    col += cool   * g2 * g2 * 0.028;

    fragColor = vec4(col, 1.0) * qt_Opacity;
}
