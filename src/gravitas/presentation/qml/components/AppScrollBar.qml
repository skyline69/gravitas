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

    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: width / 2
        color: control.pressed
            ? Theme.accent
            : (control.hovered ? Theme.borderStrong : Theme.border)
        // Fade in while scrolling/hovering, out when idle.
        opacity: control.active ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
    }

    // No visible track.
    background: null
}
