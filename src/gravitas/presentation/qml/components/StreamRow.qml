import QtQuick
import "."

Rectangle {
    id: root
    property string name
    property string subtitle
    signal clicked()
    height: 56; radius: Theme.radiusSmall
    color: mouse.containsMouse ? Theme.surfaceHover : Theme.surface
    Behavior on color { ColorAnimation { duration: Theme.durFast } }

    Column {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left; anchors.leftMargin: 12
        Text { text: root.name; color: Theme.text; font.bold: true }
        Text { text: root.subtitle; color: Theme.textDim; font.pixelSize: Theme.fontSmall }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.clicked() }
}
