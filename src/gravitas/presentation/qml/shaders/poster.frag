#version 440
// The poster with TRUE rounded-corner alpha — correct over any background,
// including the animated ambient glow. Signed-distance rounded rect with
// ~1.5px of edge smoothing.
//
// clip* mark where surrounding views' clips would slice this card (in item
// pixels); alpha feathers to nothing across fadeWidth approaching them, so
// a half-visible card dissolves instead of ending in a hard cut. The
// horizontal pair is gated by strengthLeft/strengthRight (0..1) so the
// feather can FADE in and out when a row starts or stops being scrollable —
// animating the clip position itself would visibly sweep across the card.
// The vertical pair needs no gate: a fully visible card's clip edges lie
// beyond the feather zone by construction.

layout(location = 0) in vec2 texCoord;
layout(location = 1) in vec2 itemCoord;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 itemSize;
    float radius;
    float clipLeft;
    float clipRight;
    float clipTop;
    float clipBottom;
    float fadeWidth;
    float strengthLeft;
    float strengthRight;
};
layout(binding = 1) uniform sampler2D source;

void main() {
    vec2 halfSize = itemSize * 0.5;
    vec2 q = abs(itemCoord - halfSize) - (halfSize - vec2(radius));
    float dist = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - radius;
    float alpha = 1.0 - smoothstep(-0.75, 0.75, dist);

    float fl = smoothstep(clipLeft, clipLeft + fadeWidth, itemCoord.x);
    float fr = 1.0 - smoothstep(clipRight - fadeWidth, clipRight, itemCoord.x);
    alpha *= mix(1.0, fl, strengthLeft);
    alpha *= mix(1.0, fr, strengthRight);
    alpha *= smoothstep(clipTop, clipTop + fadeWidth, itemCoord.y);
    alpha *= 1.0 - smoothstep(clipBottom - fadeWidth, clipBottom, itemCoord.y);

    fragColor = texture(source, texCoord) * alpha * qt_Opacity;
}
