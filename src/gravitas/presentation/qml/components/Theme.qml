pragma Singleton
import QtQuick

QtObject {
    // colors
    readonly property color bg: "#141414"
    readonly property color surface: "#1c1c1c"
    readonly property color surfaceHover: "#2a2a2a"
    readonly property color surfacePress: "#333333"
    readonly property color border: "#333333"
    readonly property color borderStrong: "#4a4a4a"
    readonly property color accent: "#8B5CF6"
    readonly property color accentHover: "#9d75f8"
    readonly property color text: "#f0f0f0"
    readonly property color textDim: "#9aa0a6"
    readonly property color danger: "#902020"
    // metrics
    readonly property int radius: 8
    readonly property int radiusSmall: 6
    readonly property int spacing: 8
    readonly property int controlHeight: 36
    // type
    readonly property int fontSmall: 13
    readonly property int fontBody: 15
    readonly property int fontTitle: 18
    // motion
    readonly property int durFast: 120
    readonly property int durMed: 180
}
