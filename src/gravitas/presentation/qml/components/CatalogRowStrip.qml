import QtQuick
import QtQuick.Controls

Item {
    id: root
    property string title
    property string type
    property string catalogId
    property var posters
    signal openDetail(string type, string id)
    signal seeAll(string type, string catalogId)

    implicitHeight: header.height + strip.anchors.topMargin + strip.height

    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 28

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: root.title
            color: "white"
            font.pixelSize: 18
            font.bold: true
        }

        Text {
            id: seeAllLabel
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "See All"
            color: "#9aa0a6"
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.seeAll(root.type, root.catalogId)
            }
        }
    }

    ListView {
        id: strip
        anchors.top: header.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        height: 280
        orientation: ListView.Horizontal
        spacing: 16
        clip: true
        model: root.posters
        delegate: PosterCard {
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            onClicked: root.openDetail(model.type, model.id)
        }

        // Horizontal ListViews don't scroll on a vertical mouse wheel by
        // default; map wheel delta onto contentX so a trackpad/wheel scrolls
        // the strip sideways.
        WheelHandler {
            acceptedModifiers: Qt.NoModifier
            onWheel: (event) => {
                strip.contentX = Math.max(
                    0,
                    Math.min(
                        strip.contentWidth - strip.width,
                        strip.contentX - event.angleDelta.y
                    )
                )
            }
        }
    }
}
