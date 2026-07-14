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
    // Modal: the press that dismisses the menu must NOT fall through to the
    // video underneath (it toggled pause).
    modal: true
    dim: false
    // Sized to the longest entry so labels don't clip; past the cap the row
    // text elides instead. Imperative measure — a binding that writes
    // TextMetrics.text and reads its width would retrigger itself.
    property real contentNeed: 180
    TextMetrics { id: entryMetrics; font.pixelSize: Theme.fontBody }
    onEntriesChanged: {
        var longest = 0
        for (var i = 0; i < entries.length; i++) {
            entryMetrics.text = entries[i].title
            longest = Math.max(longest, entryMetrics.advanceWidth)
        }
        // dot marker + row padding + popup padding
        contentNeed = longest + 14 + Theme.spacing * 3 + 8
    }
    width: Math.min(400, Math.max(180, contentNeed))
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
                anchors.right: parent.right
                anchors.leftMargin: Theme.spacing
                anchors.rightMargin: Theme.spacing
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
                    width: parent.width - (active ? 14 : 0)
                    text: modelData.title
                    color: active ? Theme.text : Theme.textDim
                    font.pixelSize: Theme.fontBody
                    elide: Text.ElideRight
                    maximumLineCount: 1
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
