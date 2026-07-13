import QtQuick
import QtQuick.Controls
import "."

Button {
    id: control
    property bool ghost: false
    property string iconGlyph: ""
    // Optional hover tooltip (mainly for icon-only buttons). Empty = none.
    property string tooltip: ""
    // Semantic tone: "neutral" | "positive" | "negative" | "accent". Tints the
    // label/icon and the hover/press wash.
    property string tone: "neutral"
    readonly property color toneColor: tone === "positive" ? Theme.positive
        : tone === "negative" ? Theme.negative
        : tone === "accent" ? Theme.accent
        : Theme.text
    // Persistent selected state (e.g. the gear while on the Settings page):
    // resting colour wash + tinted content, like an active nav tab.
    property bool selected: false
    readonly property color selectedColor: tone !== "neutral" ? toneColor : Theme.accent

    // A mouse click should not leave the keyboard-focus ring behind; only
    // Tab navigation shows it. StrongFocus (the default) grabs focus on click.
    focusPolicy: Qt.TabFocus

    implicitHeight: Theme.controlHeight
    padding: Theme.spacing * 1.5

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    onHoveredChanged: {
        if (control.tooltip.length === 0)
            return
        if (control.hovered)
            tipTimer.restart()
        else {
            tipTimer.stop()
            tip.close()
        }
    }
    Timer { id: tipTimer; interval: 400; onTriggered: tip.open() }
    AppToolTip {
        id: tip
        text: control.tooltip
        x: (control.width - width) / 2
        y: control.height + 8
    }

    background: Rectangle {
        radius: Theme.radius
        // Borderless soft chip, matching the nav tabs: no resting outline, just
        // a hover/press wash. Toned buttons wash in their own colour.
        readonly property color hoverWash: control.tone === "neutral"
            ? Theme.surfaceHover
            : Qt.rgba(control.toneColor.r, control.toneColor.g, control.toneColor.b, 0.16)
        readonly property color pressWash: control.tone === "neutral"
            ? Theme.surfacePress
            : Qt.rgba(control.toneColor.r, control.toneColor.g, control.toneColor.b, 0.26)
        readonly property color selectedWash: Qt.rgba(
            control.selectedColor.r, control.selectedColor.g, control.selectedColor.b, 0.16)
        color: control.pressed
            ? pressWash
            : control.hovered
                ? hoverWash
                : control.selected
                    ? selectedWash
                    : (control.ghost ? "transparent" : Theme.surface)
        border.width: control.activeFocus ? 2 : 0
        border.color: Theme.accent
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }

    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight
        // Press feedback scales only the content, so the background + its 1px
        // border stay crisp (scaling the whole button splits the antialiased
        // border into a doubled edge on the vertical sides).
        scale: control.pressed ? 0.96 : 1.0
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
        Row {
            id: row
            anchors.centerIn: parent
            spacing: (control.iconGlyph.length && control.text.length) ? Theme.spacing / 2 : 0
            AppIcon {
                // Material Symbols glyphs are em-centered, so no optical nudge
                // is needed — center on the row directly.
                anchors.verticalCenter: parent.verticalCenter
                visible: control.iconGlyph.length > 0
                glyph: control.iconGlyph
                color: control.selected ? control.selectedColor : control.toneColor
                Behavior on color { ColorAnimation { duration: Theme.durFast } }
                font.pixelSize: Theme.fontBody
                // Extra glyph-shrink press feedback ONLY for icon-only buttons;
                // labeled buttons already scale uniformly, so shrinking just the
                // icon there would desync it from the label.
                scale: (control.pressed && control.text.length === 0) ? 0.9 : 1.0
                Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                visible: control.text.length > 0
                text: control.text
                color: control.selected ? control.selectedColor : control.toneColor
                Behavior on color { ColorAnimation { duration: Theme.durFast } }
                font.pixelSize: Theme.fontBody
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }
}
