import QtQuick
import "."

Rectangle {
    id: chip
    property alias text: label.text
    implicitWidth: label.implicitWidth + 28
    implicitHeight: 34
    radius: height / 2
    color: hover.hovered ? Theme.surfaceHover : Theme.surface
    border.width: 1
    border.color: Theme.border
    Behavior on color { ColorAnimation { duration: Theme.durFast } }

    Text {
        id: label
        anchors.centerIn: parent
        color: Theme.text
        font.pixelSize: Theme.fontSmall
    }

    HoverHandler { id: hover }
}
