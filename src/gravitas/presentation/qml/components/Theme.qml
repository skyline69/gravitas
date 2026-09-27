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
    // Semantic action tones (for buttons etc.)
    readonly property color positive: "#22C55E"
    readonly property color negative: "#EF4444"
    // metrics
    readonly property int radius: 8
    readonly property int radiusSmall: 6
    readonly property int spacing: 8
    readonly property int controlHeight: 36
    // Catalog row geometry, shared by the row strip and the page view that
    // sizes its delegates before the strip exists. The strip clips, so its
    // height must hold a card's content (poster + two title lines) PLUS the
    // slack the hover scale-up grows into — a card whose content fills the
    // height gets the top of its poster shaved when hovered. A Continue
    // Watching card carries one more line (the episode subtitle), so its row
    // reserves that line on top of the same slack.
    readonly property int posterStripHeight: 300
    readonly property int posterStripSubtitleHeight: 324
    // 28px title + the 8px gap above the strip.
    readonly property int posterRowHeaderHeight: 36
    // type
    readonly property int fontSmall: 13
    readonly property int fontBody: 15
    readonly property int fontTitle: 18
    // motion
    readonly property int durFast: 120
    readonly property int durMed: 180
}
