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
    // Back by default; the player's episodes button is the same frosted disc
    // with another glyph.
    property string glyph: Icons.arrowLeft
    property string tooltip: "Back"

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
        radius: 64
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
            ? Qt.rgba(0, 0, 0, hover.hovered ? 0.38 : 0.55)
            : (hover.hovered ? Qt.rgba(1, 1, 1, 0.16) : Qt.rgba(0, 0, 0, 0.5))
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, hover.hovered ? 0.35 : 0.16)
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }

    // A change of glyph morphs rather than swaps: the old one turns away,
    // shrinking and fading, while the new one turns in from the same point.
    // The player's episodes button is the one that changes (episodes <-> x);
    // a back button never does, and never animates.
    property string _lastGlyph: ""
    Component.onCompleted: root._lastGlyph = root.glyph
    onGlyphChanged: {
        if (root._lastGlyph.length > 0 && root._lastGlyph !== root.glyph) {
            outgoing.glyph = root._lastGlyph
            morph.restart()
        }
        root._lastGlyph = root.glyph
    }
    AppIcon {
        id: outgoing
        anchors.centerIn: parent
        font.pixelSize: 20
        color: Theme.text
        opacity: 0
    }
    AppIcon {
        id: incoming
        anchors.centerIn: parent
        glyph: root.glyph
        font.pixelSize: 20
        color: Theme.text
    }
    ParallelAnimation {
        id: morph
        readonly property int duration: 220
        NumberAnimation { target: outgoing; property: "rotation"; from: 0; to: 90; duration: morph.duration; easing.type: Easing.OutCubic }
        NumberAnimation { target: outgoing; property: "scale"; from: 1; to: 0.4; duration: morph.duration; easing.type: Easing.OutCubic }
        NumberAnimation { target: outgoing; property: "opacity"; from: 1; to: 0; duration: morph.duration * 0.7; easing.type: Easing.OutQuad }
        NumberAnimation { target: incoming; property: "rotation"; from: -90; to: 0; duration: morph.duration; easing.type: Easing.OutCubic }
        NumberAnimation { target: incoming; property: "scale"; from: 0.4; to: 1; duration: morph.duration; easing.type: Easing.OutBack }
        NumberAnimation { target: incoming; property: "opacity"; from: 0; to: 1; duration: morph.duration * 0.7; easing.type: Easing.OutQuad }
    }

    HoverHandler {
        id: hover
        cursorShape: Qt.PointingHandCursor
        onHoveredChanged: {
            if (hover.hovered) {
                tipTimer.restart()
            } else {
                tipTimer.stop()
                tip.close()
            }
        }
    }
    TapHandler {
        id: tap
        // Same fix as ContextMenu/TrackMenu: the default DragThreshold policy
        // is a passive grab that never ACCEPTS the press, so the tap also
        // reached whatever this floating button covers. On Detail the button
        // sits over the scrolling episode list, so going back from a series
        // ALSO "clicked" the episode row underneath -- popping to Home while
        // pushing that episode's Sources page, which then looked like a stale
        // page wedged into the stack.
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: root.clicked()
    }
    Timer { id: tipTimer; interval: 500; onTriggered: tip.open() }
    AppToolTip {
        id: tip
        text: root.tooltip
        // Against root, not `parent`: a popup's parent is cleared while its
        // page is torn down, and these bindings re-evaluate on the way out
        // ("Cannot read property 'width' of null" on every pop).
        x: (root.width - width) / 2
        y: root.height + 8
    }
}
