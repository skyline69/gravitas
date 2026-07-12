import QtQuick
import QtQuick.Controls
import "."

Button {
    id: control
    property bool ghost: false
    property string iconGlyph: ""

    implicitHeight: Theme.controlHeight
    padding: Theme.spacing * 1.5
    scale: control.pressed ? 0.96 : 1.0
    Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }

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
