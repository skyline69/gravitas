import QtQuick
import QtQuick.Controls
import "."

// Themed tooltip matching the app surface/border language, with a scale+fade
// enter and a quick fade exit. Position it (x/y) relative to its parent and
// drive it with open()/close(); see TopBar for the hover-delay pattern.
ToolTip {
    id: control

    padding: Theme.spacing
    leftPadding: Theme.spacing * 1.25
    rightPadding: Theme.spacing * 1.25
    font.pixelSize: Theme.fontSmall

    contentItem: Text {
        text: control.text
        color: Theme.text
        font: control.font
        verticalAlignment: Text.AlignVCenter
    }

    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }

    // Scale grows from the top edge so the tip appears to drop out of the
    // element it describes.
    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        NumberAnimation { property: "scale"; from: 0.9; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        NumberAnimation { property: "y"; from: control.y - 4; to: control.y; duration: Theme.durMed; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
    }
}
