import QtQuick
import QtQuick.Controls
import "."

Slider {
    id: control
    implicitHeight: 20
    // Never grab keyboard focus on click: a focused Slider eats Space/arrow
    // keys (Space "presses" it and commits a seek on release) instead of the
    // player page's shortcuts.
    focusPolicy: Qt.NoFocus

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 4
        radius: 2
        color: Qt.rgba(1, 1, 1, 0.22)
        Rectangle {
            width: control.visualPosition * parent.width
            height: parent.height
            radius: 2
            color: Theme.accent
        }
    }

    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 14
        height: 14
        radius: 7
        color: control.pressed ? Theme.accentHover : Theme.text
        // Grows in when interacting, keeps the bar sleek at rest.
        scale: control.hovered || control.pressed ? 1.0 : 0.75
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
    }
}
