import QtQuick
import QtQuick.Controls

Item {
    id: root
    property string title
    property string posterUrl
    signal clicked()
    width: 160; height: 260

    Column {
        spacing: 6
        anchors.fill: parent
        Rectangle {
            width: 160; height: 220; radius: 8; color: "#222"
            clip: true
            Image {
                anchors.fill: parent
                source: root.posterUrl ? root.posterUrl : ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
            }
            MouseArea { anchors.fill: parent; onClicked: root.clicked() }
        }
        Text {
            width: 160; text: root.title; color: "white"
            elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WordWrap
        }
    }
}
