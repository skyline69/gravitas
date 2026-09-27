import QtQuick
import QtQuick.Controls
import "."

// Thin, rounded, auto-hiding scrollbar matching the app theme. Attach via
// `ScrollBar.vertical: AppScrollBar {}` on a ListView/GridView/Flickable.
ScrollBar {
    id: control
    policy: ScrollBar.AsNeeded
    minimumSize: 0.08
    padding: 3

    // Shown for a moment after a scroll that `active` never hears about.
    // `active` follows the Flickable's `moving`, and WheelScroller drives the
    // content position itself precisely so that the view never enters
    // `moving` (see there: a moving Flickable eats the next click). So wheel
    // and trackpad scrolling on those views left the bar hidden throughout.
    // WheelScroller calls flash() on every step instead.
    property bool _flashing: false
    function flash() {
        control._flashing = true
        hideTimer.restart()
    }
    Timer {
        id: hideTimer
        // Long enough to outlast the 180ms glide and the gap between notches
        // of one continuous scroll, so the bar holds steady rather than
        // blinking per notch.
        interval: 800
        onTriggered: control._flashing = false
    }

    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: width / 2
        color: control.pressed
            ? Theme.accent
            : (control.hovered ? Theme.borderStrong : Theme.border)
        // Fade in while scrolling/hovering, out when idle.
        opacity: control.active || control._flashing ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }

    // No visible track.
    background: null
}
