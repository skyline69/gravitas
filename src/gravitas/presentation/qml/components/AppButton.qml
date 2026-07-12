import QtQuick
import QtQuick.Controls
import "."

Button {
    id: control
    property bool ghost: false

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

    contentItem: Text {
        text: control.text
        color: Theme.text
        font.pixelSize: Theme.fontBody
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
