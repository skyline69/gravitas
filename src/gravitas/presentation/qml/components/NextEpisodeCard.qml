pragma ComponentBehavior: Bound
import QtQuick
import Qt5Compat.GraphicalEffects
import "."

// The player's "next episode" offer, shown over the end of an episode: the
// next one's still, whether it continues the season or starts the next, its
// label, and a play mark. Flies in from the right with a fade, and back out
// the same way. The player decides when it is shown and what a click does.
Item {
    id: root
    property bool shown: false
    // DetailController.nextEpisode(): videoId, label, thumbnail, newSeason, season.
    property var episode: ({})
    // Fetching the next episode's sources after a click.
    property bool busy: false
    // Why the last click could not play; replaces the caption while set.
    property string message: ""
    signal clicked()

    implicitWidth: 340
    implicitHeight: 84

    property real reveal: shown ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.durMed * 2; easing.type: Easing.OutCubic } }
    opacity: reveal
    visible: reveal > 0
    transform: Translate { x: (1 - root.reveal) * 72 }

    Rectangle {
        id: card
        anchors.fill: parent
        radius: 12
        color: hover.hovered ? Qt.rgba(0.12, 0.12, 0.12, 0.94) : Qt.rgba(0.078, 0.078, 0.078, 0.9)
        border.width: 1
        border.color: hover.hovered ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.8)
                                    : Theme.borderStrong
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
        scale: tap.pressed ? 0.97 : 1
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
        layer.enabled: true
        layer.effect: DropShadow {
            verticalOffset: 4
            radius: 18
            samples: 37
            color: Qt.rgba(0, 0, 0, 0.5)
            transparentBorder: true
        }
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler {
        id: tap
        // Taken, not shared: the video underneath pauses on a click.
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: if (!root.busy) root.clicked()
    }

    Item {
        id: still
        x: 10
        anchors.verticalCenter: parent.verticalCenter
        width: 112
        height: 63
        Rectangle { anchors.fill: parent; radius: 8; color: Theme.surfaceHover }
        Image {
            id: thumb
            anchors.fill: parent
            source: root.episode.thumbnail ? Img.sized(root.episode.thumbnail, 320) : ""
            sourceSize.width: 320
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            opacity: status === Image.Ready ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
            layer.enabled: true
            layer.effect: OpacityMask {
                maskSource: Rectangle { width: thumb.width; height: thumb.height; radius: 8 }
            }
        }
        // Play mark, or a spinner while the next episode's sources are found.
        Rectangle {
            anchors.centerIn: parent
            width: 30; height: 30
            radius: 15
            color: Qt.rgba(0, 0, 0, 0.55)
            AppIcon {
                anchors.centerIn: parent
                visible: !root.busy
                glyph: Icons.play
                font.pixelSize: 20
                color: "white"
            }
            AppSpinner {
                anchors.centerIn: parent
                width: 18; height: 18
                running: root.busy
                visible: root.busy
            }
        }
    }

    Column {
        anchors.left: still.right
        anchors.leftMargin: 12
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3
        Text {
            width: parent.width
            text: root.message.length > 0 ? root.message
                : root.episode.newSeason ? "Start season " + root.episode.season
                : "Next episode"
            color: root.message.length > 0 ? Theme.negative : Theme.accentHover
            font.pixelSize: Theme.fontSmall
            font.bold: root.message.length === 0
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
        Text {
            width: parent.width
            text: root.episode.label || ""
            color: Theme.text
            font.pixelSize: Theme.fontBody
            font.bold: true
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
    }
}
