import QtQuick

Rectangle {
    id: root
    property string name
    property string subtitle
    signal clicked()
    height: 56; radius: 6
    color: mouse.containsMouse ? "#333" : "#1c1c1c"

    Column {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left; anchors.leftMargin: 12
        Text { text: root.name; color: "white"; font.bold: true }
        Text { text: root.subtitle; color: "#aaa"; font.pixelSize: 12 }
    }
    MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; onClicked: root.clicked() }
}
