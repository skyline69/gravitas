import QtQuick
import QtQuick.Controls

Item {
    id: root
    property string title
    property string addonId
    property string type
    property string catalogId
    property var posters
    signal openDetail(string type, string id)
    signal seeAll(string addonId, string type, string catalogId)

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
                onClicked: root.seeAll(root.addonId, root.type, root.catalogId)
            }
        }
    }

    ListView {
        id: strip
        anchors.top: header.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        height: 300
        orientation: ListView.Horizontal
        spacing: 16
        clip: true
        // inset the content from the clip edges so the first/last card has
        // room to grow on hover without being clipped
        leftMargin: 10
        rightMargin: 10
        model: root.posters
        delegate: PosterCard {
            height: strip.height
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            mediaType: model.type
            onClicked: root.openDetail(model.type, model.id)
        }

        // Scroll the strip sideways only on a horizontal wheel/trackpad
        // gesture. A vertical wheel is left unaccepted so it bubbles up to
        // the outer rows ListView and scrolls the page — otherwise hovering
        // any strip would swallow page scrolling.
        WheelHandler {
            acceptedModifiers: Qt.NoModifier
            onWheel: (event) => {
                if (Math.abs(event.angleDelta.x) > Math.abs(event.angleDelta.y)) {
                    strip.contentX = Math.max(
                        0,
                        Math.min(
                            Math.max(0, strip.contentWidth - strip.width),
                            strip.contentX - event.angleDelta.x
                        )
                    )
                } else {
                    event.accepted = false
                }
            }
        }
    }
}
