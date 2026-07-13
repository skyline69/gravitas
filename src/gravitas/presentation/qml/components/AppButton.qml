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
    scale: control.pressed ? 0.96 : 1.0
    Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: Theme.radius
        color: control.ghost
            ? (control.pressed ? Theme.surfacePress : control.hovered ? Theme.surfaceHover : "transparent")
            : (control.pressed ? Theme.surfacePress : control.hovered ? Theme.surfaceHover : Theme.surface)
        // A visible resting outline separates the control from whatever it
        // sits on (page bg or the surface-colored top bar), so interactive
        // elements never blend into their background. Accent ring on focus.
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accent : Theme.borderStrong
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }

    contentItem: Item {
        implicitWidth: row.implicitWidth
        implicitHeight: row.implicitHeight
        Row {
            id: row
            anchors.centerIn: parent
            spacing: (control.iconGlyph.length && control.text.length) ? Theme.spacing / 2 : 0
            AppIcon {
                anchors.verticalCenter: parent.verticalCenter
                // Icon glyphs are centered on their line box, but adjacent text
                // sits lower (baseline/x-height), so nudge the icon down a hair
                // to optically align with the label.
                anchors.verticalCenterOffset: Math.round(Theme.fontBody * 0.1)
                visible: control.iconGlyph.length > 0
                glyph: control.iconGlyph
                color: Theme.text
                font.pixelSize: Theme.fontBody
                // Pronounced press feedback on the glyph itself (on top of the
                // button's own scale), so icon-only buttons read as "pressed".
                scale: control.pressed ? 0.9 : 1.0
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
