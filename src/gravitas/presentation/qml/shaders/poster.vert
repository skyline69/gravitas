#version 440
// Rounded-poster pair (with poster.frag): forwards the source texture
// coordinate untouched (required for supportsAtlasTextures) and hands the
// fragment stage the position in ITEM pixels, which is what the rounded
// corner is defined in.

layout(location = 0) in vec4 qt_Vertex;
layout(location = 1) in vec2 qt_MultiTexCoord0;
layout(location = 0) out vec2 texCoord;
layout(location = 1) out vec2 itemCoord;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 itemSize;
    float radius;
};

void main() {
    texCoord = qt_MultiTexCoord0;
    itemCoord = qt_Vertex.xy;
    gl_Position = qt_Matrix * qt_Vertex;
}
