import QtQuick
import QtQuick.Controls
import "."

// Modal confirm for destructive actions. `ask()` opens it centred on the
// window; the confirm button emits confirmed().
Popup {
    id: dialog
    property string heading: ""
    property string body: ""
    property string confirmText: "Delete"
    signal confirmed()

    function ask() {
        dialog.open()
    }

    // A Popup is not an Item, so it has no Window attached property — reaching
    // for Window.window here warns and evaluates to null. Overlay is the
    // supported way for a popup to see its window, and centreing on it also
    // reparents the dialog to the window overlay. The overlay is 0x0 until the
    // window is shown, hence the width guard.
    anchors.centerIn: Overlay.overlay
    modal: true
    dim: true
    padding: 24
    // Not `availableWidth` — Popup already declares that FINAL (width minus
    // padding), and shadowing it makes the whole component unloadable.
    readonly property real widthBudget: Overlay.overlay && Overlay.overlay.width > 0
        ? Overlay.overlay.width - 48
        : 420
    width: Math.min(420, widthBudget)
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        radius: Theme.radius * 1.5
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
        spacing: 16
        Text {
            width: parent.width
            text: dialog.heading
            color: Theme.text
            font.pixelSize: Theme.fontTitle
            font.bold: true
            wrapMode: Text.WordWrap
        }
        Text {
            width: parent.width
            visible: text.length > 0
            text: dialog.body
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }
        Row {
            anchors.right: parent.right
            spacing: 8
            AppButton {
                text: "Cancel"
                ghost: true
                onClicked: dialog.close()
            }
            AppButton {
                text: dialog.confirmText
                tone: "negative"
                onClicked: {
                    dialog.close()
                    dialog.confirmed()
                }
            }
        }
    }
}
