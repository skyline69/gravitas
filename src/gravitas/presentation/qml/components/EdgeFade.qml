import QtQuick
import "."

// Feathered edges for a scrolling view, used as its layer effect:
//
//     ListView {
//         id: list
//         layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
//         layer.effect: EdgeFade { view: list }
//     }
//
// The top and bottom feather only while something is scrolled past that edge
// -- a list at rest with its first row in full view has nothing to dissolve
// into -- and fade in and out as that changes. Anything that must stay crisp
// at the edges (the scroll bar) has to live outside the view, since the
// effect feathers everything the view draws. See shaders/edgefade.frag.
ShaderEffect {
    id: root
    required property Flickable view
    property variant source
    property vector2d itemSize: Qt.vector2d(width, height)
    property real fadeWidth: 40
    property real topStrength: root.view.atYBeginning ? 0 : 1
    property real bottomStrength: root.view.atYEnd ? 0 : 1
    Behavior on topStrength { NumberAnimation { duration: Theme.durMed } }
    Behavior on bottomStrength { NumberAnimation { duration: Theme.durMed } }
    fragmentShader: Qt.resolvedUrl("../shaders/edgefade.frag.qsb")
}
