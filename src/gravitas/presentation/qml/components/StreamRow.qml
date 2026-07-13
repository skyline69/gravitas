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
        anchors.right: parent.right; anchors.rightMargin: 12
        // Addon-supplied labels: hard-cap to one elided line each so a rogue
        // multi-line name can't overflow the fixed row height.
        Text {
            width: parent.width
            text: root.name
            color: Theme.text
            font.bold: true
            elide: Text.ElideRight
            maximumLineCount: 1
            textFormat: Text.PlainText
        }
        Text {
            width: parent.width
            text: root.subtitle
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            maximumLineCount: 1
            textFormat: Text.PlainText
        }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.clicked() }
}
