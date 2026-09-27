import QtQuick
import Qt5Compat.GraphicalEffects
import "."

// "Skip intro" / "Skip recap": shown while playback is inside a section the
// file's own chapters named. Flies in from the right with a fade, like the
// next-episode card it shares a corner with (never at the same time: one is at
// the start of an episode, the other at its end).
Item {
    id: root
    property bool shown: false
    property string label: ""
    signal clicked()

    implicitWidth: row.implicitWidth + 36
    implicitHeight: 44

    property real reveal: shown ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.durMed * 2; easing.type: Easing.OutCubic } }
    opacity: reveal
    visible: reveal > 0
    transform: Translate { x: (1 - root.reveal) * 56 }

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: hover.hovered ? Qt.rgba(0.12, 0.12, 0.12, 0.94) : Qt.rgba(0.078, 0.078, 0.078, 0.9)
        border.width: 1
        border.color: hover.hovered ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.8)
                                    : Theme.borderStrong
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
        scale: tap.pressed ? 0.96 : 1
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
        layer.enabled: true
        layer.effect: DropShadow {
            verticalOffset: 4
            radius: 16
            samples: 33
            color: Qt.rgba(0, 0, 0, 0.5)
            transparentBorder: true
        }
    }

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 8
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            color: Theme.text
            font.pixelSize: Theme.fontBody
            font.bold: true
        }
        AppIcon {
            anchors.verticalCenter: parent.verticalCenter
            glyph: Icons.fastForward
            font.pixelSize: 20
            color: Theme.accentHover
        }
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler {
        id: tap
        // Taken, not shared: the video underneath pauses on a click.
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: root.clicked()
    }
}
