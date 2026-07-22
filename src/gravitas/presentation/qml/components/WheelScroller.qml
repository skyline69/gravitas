import QtQuick
import "."

// Wheel scrolling that doesn't swallow the click after it.
//
// Flickable's own wheel handling runs its kinetic machinery: `moving` stays
// true for ~300ms after the last notch, and Flickable eats any press that
// lands in that window to stop the motion instead of letting it reach the row
// under the cursor. That is the "I have to click the episode twice" bug --
// scroll the list, click straight away, the first click only stops the scroll.
//
// Driving the content position ourselves keeps a smooth, animated scroll while
// the Flickable never enters `moving`, so a click always lands. Declare it
// inside the Flickable/ListView/GridView it should drive:
//
//     ListView { id: list; WheelScroller { flick: list } }
WheelHandler {
    id: root

    required property Flickable flick
    // The axis this drives. The other one is left to the Flickable.
    property bool horizontal: false
    // Pixels per wheel notch (120 angle units), ~ Qt's own three lines.
    property real step: 90

    // With nothing to scroll the wheel must fall through to whatever is behind
    // this surface -- the horizontal poster strips inside Home's vertical list
    // scroll the page that way.
    enabled: horizontal
        ? flick.contentWidth + flick.leftMargin + flick.rightMargin > flick.width
        : flick.contentHeight + flick.topMargin + flick.bottomMargin > flick.height
    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    // Only this axis is ours. Without it the handler consumes the other one
    // too, and a vertical wheel over a horizontal poster strip would stop
    // dead instead of scrolling the page the strip sits in.
    orientation: horizontal ? Qt.Horizontal : Qt.Vertical

    property NumberAnimation glide: NumberAnimation {
        target: root.flick
        property: root.horizontal ? "contentX" : "contentY"
        duration: Theme.durMed
        easing.type: Easing.OutCubic
    }

    // A drag (or the flick it throws) owns the content position outright; an
    // in-flight glide would fight it for its remaining frames.
    property Connections dragWins: Connections {
        target: root.flick
        function onDraggingChanged() { if (root.flick.dragging) root.glide.stop() }
    }

    onWheel: (event) => {
        // Trackpads send pixelDelta (already in content pixels); mouse wheels
        // send angleDelta in eighths of a degree, 120 per notch.
        const pixel = root.horizontal ? event.pixelDelta.x : event.pixelDelta.y
        const angle = root.horizontal ? event.angleDelta.x : event.angleDelta.y
        const delta = pixel !== 0 ? pixel : angle / 120 * root.step
        if (delta === 0)
            return
        // Margins are part of the travel: a page with `topMargin: 80` rests at
        // contentY -80, so clamping at originY would pin it 80px scrolled in.
        const origin = root.horizontal ? root.flick.originX : root.flick.originY
        const span = root.horizontal
            ? root.flick.contentWidth - root.flick.width
            : root.flick.contentHeight - root.flick.height
        const lead = root.horizontal ? root.flick.leftMargin : root.flick.topMargin
        const trail = root.horizontal ? root.flick.rightMargin : root.flick.bottomMargin
        // Chain onto the glide already running, so a fast burst of notches
        // scrolls the sum of them rather than restarting from where the
        // animation happens to be.
        const from = root.glide.running
            ? root.glide.to
            : (root.horizontal ? root.flick.contentX : root.flick.contentY)
        const min = origin - lead
        const max = origin + Math.max(0, span) + trail
        root.glide.to = Math.max(min, Math.min(max, from - delta))
        root.glide.restart()
    }
}
