#version 440
// Ambient background: two slow glows drifting over the page colour.
// Gaussian falloff (exp of squared distance) has no start/end edge to see,
// and a per-pixel dither breaks the 8-bit banding that a smooth dark
// gradient otherwise shows as visible "waves". Compiled with
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

    float d1 = distance(uv, c1);
    float d2 = distance(uv, c2);
    float g1 = exp(-d1 * d1 * 3.0);
    float g2 = exp(-d2 * d2 * 2.4);

    vec3 base = vec3(0.0784, 0.0784, 0.0784);   // Theme.bg #141414
    vec3 accent = vec3(0.545, 0.361, 0.965);    // Theme.accent #8B5CF6
    vec3 cool   = vec3(0.231, 0.510, 0.965);    // a quiet blue

    vec3 col = base;
    col += accent * g1 * 0.085;
    col += cool   * g2 * 0.058;

    // Dither: +-half an 8-bit step of stationary hash noise. Costs nothing,
    // invisible by itself, and turns banded rings into smooth falloff.
    float n = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
    col += (n - 0.5) / 255.0;

    fragColor = vec4(col, 1.0) * qt_Opacity;
}
