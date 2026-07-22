import QtQuick
import QtQuick.Controls as QQC
import "."

// Qualified Controls import on purpose: `ContextMenu` below is Qt's attached
// type, and this file also imports "." -- which holds a ContextMenu component
// of ours. Unqualified, the two collide and the attached property never binds.
QQC.TextField {
    id: control
    implicitHeight: Theme.controlHeight
    leftPadding: Theme.spacing * 1.5
    rightPadding: Theme.spacing * 1.5
    color: Theme.text
    placeholderTextColor: Theme.textDim
    selectionColor: Theme.accent
    selectedTextColor: Theme.text
    font.pixelSize: Theme.fontBody

    // Off with Qt's built-in editing menu -- native and unstyleable on
    // Windows -- and on with ours. See TextEditMenu.
    QQC.ContextMenu.menu: null
    TextEditMenu { editor: control }

    background: Rectangle {
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: control.activeFocus ? Theme.accent : Theme.borderStrong
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }
}
