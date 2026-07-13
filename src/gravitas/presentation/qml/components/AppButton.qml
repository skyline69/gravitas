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
        border.width: control.activeFocus ? 2 : (control.ghost ? 1 : 0)
        border.color: control.activeFocus ? Theme.accent : Theme.border
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
