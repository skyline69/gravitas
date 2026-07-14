import QtQuick
import Qt5Compat.GraphicalEffects
import "."

// Circular floating back button with a frosted-glass backdrop: the content
// behind (pass it as blurTarget) is sampled live, blurred, and masked to the
// disc. Without a blurTarget it falls back to a translucent dark disc.
Item {
    id: root
    signal clicked()
    // The item rendered BENEATH this button (page content / video). Must not
    // contain this button, or the effect source recurses.
    property Item blurTarget: null

    width: 42
    height: 42
    scale: tap.pressed ? 0.9 : (hover.hovered ? 1.08 : 1.0)
    Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }

    ShaderEffectSource {
        id: grab
        anchors.fill: parent
        visible: false
        live: true
        sourceItem: root.blurTarget
        sourceRect: {
            if (!root.blurTarget)
                return Qt.rect(0, 0, 0, 0)
            var p = root.blurTarget.mapFromItem(root, 0, 0)
            return Qt.rect(p.x, p.y, root.width, root.height)
        }
    }
    FastBlur {
        id: frost
        anchors.fill: parent
        source: grab
        radius: 36
        visible: false
    }
    Rectangle { id: discMask; anchors.fill: parent; radius: width / 2; visible: false }
    OpacityMask {
        anchors.fill: parent
        source: frost
        maskSource: discMask
        visible: root.blurTarget !== null
    }

    // Tint + rim over the frost (solid fallback when there is no blurTarget).
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.blurTarget !== null
            ? Qt.rgba(0, 0, 0, hover.hovered ? 0.18 : 0.32)
            : (hover.hovered ? Qt.rgba(1, 1, 1, 0.16) : Qt.rgba(0, 0, 0, 0.5))
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, hover.hovered ? 0.35 : 0.16)
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }

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
