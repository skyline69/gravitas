pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "."

// Right-click / long-press menu. The project builds its own controls rather
// than using stock QtQuick.Controls Menu (which ignores our styling on some
// platform styles), so this follows TrackMenu's themed-Popup pattern.
// `entries` is a list of { label, action } — action is a JS function.
Popup {
    id: menu
    property var entries: []

    // Opens with its top-left at the cursor, clamped inside the window.
    // Set `entries` before calling: the clamp reads menu.width, which is sized
    // from them.
    //
    // A Popup is not an Item, so it has no Window attached property — reaching
    // for Window.window here warns and evaluates to null. Overlay is the
    // supported way for a popup to see its window, and it spans the window, so
    // it doubles as the coordinate space to map into and the bounds to clamp
    // against.
    function popupAt(item, position) {
        var overlay = menu.Overlay.overlay
        if (!overlay) {
            menu.open()
            return
        }
        menu.parent = overlay
        var point = item.mapToItem(overlay, position.x, position.y)
        menu.x = Math.max(8, Math.min(point.x, overlay.width - menu.width - 8))
        menu.y = Math.max(8, Math.min(point.y, overlay.height - menu.implicitHeight - 8))
        menu.open()
    }

    modal: true
    dim: false
    padding: 4
    width: Math.min(320, Math.max(180, contentNeed))

    // Imperative measure — a binding that writes TextMetrics.text and reads
    // its width would retrigger itself (same reasoning as TrackMenu).
    property real contentNeed: 180
    TextMetrics { id: entryMetrics; font.pixelSize: Theme.fontBody }
    onEntriesChanged: {
        var longest = 0
        for (var i = 0; i < entries.length; i++) {
            entryMetrics.text = entries[i].label
            longest = Math.max(longest, entryMetrics.advanceWidth)
        }
        contentNeed = longest + Theme.spacing * 3
    }

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

    contentItem: Column {
        spacing: 2
        Repeater {
            model: menu.entries
            Rectangle {
                id: entry
                required property var modelData
                width: menu.width - 8
                height: Theme.controlHeight
                radius: Theme.radiusSmall
                color: rowHover.hovered ? Theme.surfaceHover : "transparent"
                HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    // ReleaseWithinBounds takes the exclusive grab at press.
                    // The default (DragThreshold) is a passive grab that never
                    // ACCEPTS the event, so the same click also reached
                    // whatever sat under the popup — picking "Mark as
                    // watched" on an episode row simultaneously "clicked" the
                    // row and pushed the Sources page underneath the menu.
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: {
                        menu.close()
                        entry.modelData.action()
                    }
                }
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.spacing
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.spacing
                    anchors.verticalCenter: parent.verticalCenter
                    text: entry.modelData.label
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    elide: Text.ElideRight
                }
            }
        }
    }
}
