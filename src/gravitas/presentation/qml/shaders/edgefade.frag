#version 440
// A scrolling view's top and bottom edges, feathered: applied as the view's layer.effect,
// so whatever is under an edge -- a source row, its chips, its outline --
// dissolves into the page instead of ending in the view's hard clip. The
// same feather Home's posters get from poster.frag, but per view rather than
// per item, because a row is text and rectangles, not one texture.
//
// Only top and bottom: rows never scroll off sideways, so a side feather has
// nothing to dissolve and just reads as the row being cut off. Each edge is
// gated by a strength (0..1, animated by EdgeFade.qml) so an edge with
// nothing scrolled past it shows no feather, and the feather FADES in and out
// rather than sweeping across the rows. Compiled with scripts/build_shaders.sh.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 itemSize;
    float fadeWidth;
    float topStrength;
    float bottomStrength;
};
layout(binding = 1) uniform sampler2D source;

void main() {
    vec2 p = qt_TexCoord0 * itemSize;
    float alpha = mix(1.0, smoothstep(0.0, fadeWidth, p.y), topStrength);
    alpha *= mix(1.0, 1.0 - smoothstep(itemSize.y - fadeWidth, itemSize.y, p.y),
                 bottomStrength);
    fragColor = texture(source, qt_TexCoord0) * alpha * qt_Opacity;
}
