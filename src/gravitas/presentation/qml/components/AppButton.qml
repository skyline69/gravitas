import QtQuick
import QtQuick.Controls
import "."

Button {
    id: control
    property bool ghost: false
    property string iconGlyph: ""

    // A mouse click should not leave the keyboard-focus ring behind; only
    // Tab navigation shows it. StrongFocus (the default) grabs focus on click.
    focusPolicy: Qt.TabFocus

    implicitHeight: Theme.controlHeight
    padding: Theme.spacing * 1.5

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: Theme.radius
        color: control.ghost
            ? (control.pressed ? Theme.surfacePress : control.hovered ? Theme.surfaceHover : "transparent")
            : (control.pressed ? Theme.surfacePress : control.hovered ? Theme.surfaceHover : Theme.surface)
        // Borderless soft chip, matching the nav tabs: no resting outline,
        // just a hover/press wash. Accent ring appears only on keyboard focus.
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
                color: Theme.text
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
                color: Theme.text
                font.pixelSize: Theme.fontBody
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }
}
