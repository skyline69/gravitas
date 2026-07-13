import QtQuick
import QtQuick.Controls
import "."

// Popup listing playable tracks (audio or subtitles). Opens above its parent
// button. `tracks` is a list of {id, title}; picking emits picked(id).
Popup {
    id: menu
    property var tracks: []
    property int currentId: 0
    property bool offEntry: false
    signal picked(int id)

    readonly property var entries: offEntry
        ? [{ id: -1, title: "Off" }].concat(tracks)
        : tracks

    x: parent ? (parent.width - width) / 2 : 0
    y: -implicitHeight - 8
    width: 240
    padding: 4

    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
    }

    contentItem: ListView {
        implicitHeight: Math.min(contentHeight, 280)
        clip: true
        model: menu.entries
        ScrollBar.vertical: AppScrollBar {}
        delegate: Rectangle {
            required property var modelData
            readonly property bool active: modelData.id === menu.currentId
            width: ListView.view.width
            height: Theme.controlHeight
            radius: Theme.radiusSmall
            color: rowHover.hovered ? Theme.surfaceHover : "transparent"
            HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                onTapped: {
                    menu.picked(modelData.id)
                    menu.close()
                }
            }
            Row {
                anchors.left: parent.left
                anchors.leftMargin: Theme.spacing
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 6; height: 6; radius: 3
                    color: Theme.accent
                    visible: active
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.title
                    color: active ? Theme.text : Theme.textDim
                    font.pixelSize: Theme.fontBody
                }
            }
        }
        Text {
            anchors.centerIn: parent
            visible: menu.entries.length === 0
            text: "No tracks"
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
        }
    }
}
