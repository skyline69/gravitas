#version 440
// The poster with TRUE rounded-corner alpha — correct over any background,
// including the animated ambient glow (the old trick painted bg-coloured
// corner notches, which turned visible the moment the background stopped
// being flat). Signed-distance rounded rect with ~1.5px of edge smoothing.

layout(location = 0) in vec2 texCoord;
layout(location = 1) in vec2 itemCoord;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 itemSize;
    float radius;
};
layout(binding = 1) uniform sampler2D source;

void main() {
    vec2 halfSize = itemSize * 0.5;
    vec2 q = abs(itemCoord - halfSize) - (halfSize - vec2(radius));
    float dist = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - radius;
    float alpha = 1.0 - smoothstep(-0.75, 0.75, dist);
    fragColor = texture(source, texCoord) * alpha * qt_Opacity;
}
