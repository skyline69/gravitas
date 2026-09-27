import QtQuick
import QtQuick.Controls
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
//
// A HORIZONTAL one nested inside a vertical view (a poster strip on Home) also
// scrolls the view it sits in, for gestures aimed that way. It has to: a
// WheelHandler that takes an event keeps the grab while the wheel keeps
// turning, so the outer view's own handler is no longer offered any of them --
// which is the "hover a row and I can't scroll the page" bug -- and handing one
// back with `event.accepted = false` does not restore that delivery.
WheelHandler {
    id: root

    required property Flickable flick
    // The axis this drives. The other one is left to the Flickable, unless
    // `outer` picks it up (below).
    property bool horizontal: false
    // Pixels per wheel notch (120 angle units), ~ Qt's own three lines.
    property real step: 90

    // With nothing to scroll the wheel must fall through to whatever is behind
    // this surface -- a strip too short to scroll never grabs, so the page
    // under it keeps its own wheel handling.
    enabled: horizontal
        ? flick.contentWidth + flick.leftMargin + flick.rightMargin > flick.width
        : flick.contentHeight + flick.topMargin + flick.bottomMargin > flick.height
    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
    // BOTH axes are wanted, deliberately. Filtering to one with `orientation`
    // is what breaks the nesting: the handler still grabs on the gestures it
    // does want, and then silently drops every event on the other axis instead
    // of letting the page have it. Which axis does what is decided in onWheel.

    // The scrollable view this one is nested inside, found by walking up from
    // `flick` on first use (the parent chain is not complete while a delegate
    // is still being built). Only horizontal scrollers look: a strip forwards
    // vertical gestures to its page. `null` = none found, `undefined` = not
    // looked yet.
    property var _outer: undefined
    function outer() {
        if (root._outer === undefined) {
            root._outer = null
            // `as` answers null for anything that is not a Flickable, which
            // is the whole question the walk asks.
            for (let p = root.flick.parent; p; p = p.parent) {
                const page = p as Flickable
                if (page) {
                    root._outer = page
                    break
                }
            }
        }
        return root._outer
    }

    property NumberAnimation glide: NumberAnimation {
        target: root.flick
        property: root.horizontal ? "contentX" : "contentY"
        duration: Theme.durMed
        easing.type: Easing.OutCubic
    }

    // The same glide for the page a strip forwards to. Its target is only
    // known at wheel time, so it cannot be bound.
    property NumberAnimation outerGlide: NumberAnimation {
        property: "contentY"
        duration: Theme.durMed
        easing.type: Easing.OutCubic
    }

    // Wake the view's scrollbar: it only shows by itself while the view is
    // `moving`, which these scrolls deliberately never trigger. Any bar that
    // offers flash() (AppScrollBar) is told; anything else is left alone.
    function reveal(f: Flickable, sideways: bool) {
        const bar = (sideways ? f.ScrollBar.horizontal : f.ScrollBar.vertical) as AppScrollBar
        if (bar)
            bar.flash()
    }

    // A drag (or the flick it throws) owns the content position outright; an
    // in-flight glide would fight it for its remaining frames.
    property Connections dragWins: Connections {
        target: root.flick
        function onDraggingChanged() { if (root.flick.dragging) root.glide.stop() }
    }

    // Writing contentX/Y under an in-flight flick (Home drives one from its
    // own mouse-wheel overlay) has the two fighting for the same property,
    // frame by frame -- which reads as the scroll catching and stuttering.
    // The gesture is the newer intent, so the flick yields to it.
    function takeOver(f) {
        if (f.flicking)
            f.cancelFlick()
    }

    // Where `f` may travel on this axis. Margins are part of it: a page with
    // `topMargin: 80` rests at contentY -80, so clamping at originY would pin
    // it 80px scrolled in.
    function limit(f, sideways, position) {
        const origin = sideways ? f.originX : f.originY
        const span = sideways ? f.contentWidth - f.width : f.contentHeight - f.height
        const lead = sideways ? f.leftMargin : f.topMargin
        const trail = sideways ? f.rightMargin : f.bottomMargin
        return Math.max(origin - lead,
                        Math.min(origin + Math.max(0, span) + trail, position))
    }

    onWheel: (event) => {
        // Trackpads send pixelDelta (already in content pixels); mouse wheels
        // send angleDelta in eighths of a degree, 120 per notch.
        const pixels = event.pixelDelta.x !== 0 || event.pixelDelta.y !== 0
        const dx = pixels ? event.pixelDelta.x : event.angleDelta.x / 120 * root.step
        const dy = pixels ? event.pixelDelta.y : event.angleDelta.y / 120 * root.step
        const mine = root.horizontal ? dx : dy
        const other = root.horizontal ? dy : dx
        // Axis dominance. A two-finger swipe is never perfectly on-axis -- a
        // macOS trackpad reports a few pixels of drift on the other axis with
        // every event -- so "is my axis non-zero" would let a scroll DOWN walk
        // a poster strip sideways. The dominant axis wins outright.
        if (Math.abs(other) > Math.abs(mine)) {
            const page = root.horizontal ? root.outer() : null
            if (!page) {
                event.accepted = false
                return
            }
            // Vertical gesture over a strip: scroll the page it sits in, since
            // it will not be offered this event itself. Same two speeds as the
            // owned axis below — 1:1 for a pixel-precise stream, an eased
            // glide for a discrete notch.
            root.reveal(page, false)
            if (pixels) {
                root.outerGlide.stop()
                root.takeOver(page)
                page.contentY = root.limit(page, false, page.contentY - other)
                return
            }
            const base = (root.outerGlide.running && root.outerGlide.target === page)
                ? root.outerGlide.to : page.contentY
            root.outerGlide.target = page
            root.outerGlide.to = root.limit(page, false, base - other)
            root.outerGlide.restart()
            return
        }
        if (mine === 0) {
            event.accepted = false
            return
        }
        // Chain onto the glide already running, so a fast burst of notches
        // scrolls the sum of them rather than restarting from where the
        // animation happens to be.
        const from = (!pixels && root.glide.running)
            ? root.glide.to
            : (root.horizontal ? root.flick.contentX : root.flick.contentY)
        const to = root.limit(root.flick, root.horizontal, from - mine)
        root.reveal(root.flick, root.horizontal)
        if (pixels) {
            // A pixel-precise device (trackpad) already sends a smooth stream,
            // including its own momentum tail: the gesture IS the animation.
            // Feeding each event through a 180ms eased glide adds that lag to
            // every one of them and mushes the tail into a rubbery drift --
            // the finger stops and the view keeps easing. Move 1:1 instead.
            root.glide.stop()
            root.takeOver(root.flick)
            if (root.horizontal)
                root.flick.contentX = to
            else
                root.flick.contentY = to
            return
        }
        root.glide.to = to
        root.glide.restart()
    }
}
