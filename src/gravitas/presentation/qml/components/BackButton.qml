import QtQuick
import "."

// Circular floating back button: glassy dark disc that brightens and grows
// slightly on hover, shrinks on press. Legible over any artwork or video.
Rectangle {
    id: root
    signal clicked()

    width: 42
    height: 42
    radius: width / 2
    color: hover.hovered ? Qt.rgba(1, 1, 1, 0.16) : Qt.rgba(0, 0, 0, 0.5)
    border.width: 1
    border.color: Qt.rgba(1, 1, 1, hover.hovered ? 0.35 : 0.16)
    scale: tap.pressed ? 0.9 : (hover.hovered ? 1.08 : 1.0)
    Behavior on color { ColorAnimation { duration: Theme.durFast } }
    Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }

    AppIcon {
        anchors.centerIn: parent
        glyph: Icons.arrowLeft
        font.pixelSize: 20
        color: Theme.text
    }

    HoverHandler {
        id: hover
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: hovered ? tipTimer.restart() : (tipTimer.stop(), tip.close())
    }
    TapHandler { id: tap; onTapped: root.clicked() }
    Timer { id: tipTimer; interval: 500; onTriggered: tip.open() }
    AppToolTip {
        id: tip
        text: "Back"
        x: (parent.width - width) / 2
        y: parent.height + 8
    }
}
