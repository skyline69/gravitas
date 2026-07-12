import QtQuick
import QtQuick.Controls
import "."

TextField {
    id: control
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spacing * 1.5
    rightPadding: Theme.spacing * 1.5
    color: Theme.text
    placeholderTextColor: Theme.textDim
    selectionColor: Theme.accent
    selectedTextColor: Theme.text
    font.pixelSize: Theme.fontBody

    background: Rectangle {
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: control.activeFocus ? Theme.accent : Theme.border
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }
}
