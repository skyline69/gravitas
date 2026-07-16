import QtQuick
import QtQuick.Controls
import "."

// Confirmation for a stremio:// install link. A web page asked to add a data
// source to this app, so the user sees who is asking -- the HOST, which is the
// one part of this a page cannot make up -- before anything is installed.
Popup {
    id: dialog

    // Filled from DeepLinkController.installRequested's payload.
    property string addonName: ""
    property string addonVersion: ""
    property string host: ""
    property string addonDescription: ""
    property bool adult: false
    property bool p2p: false
    property bool configurationRequired: false
    property string configureUrl: ""

    signal confirmed()
    signal cancelled()
    signal configureRequested(string url)

    // Whether the user chose something. close() finishes asynchronously (after
    // the exit transition), so without this an accepted dialog would emit
    // cancelled() AFTER confirmed() and clear the pending link out from under
    // the install -- a click that silently did nothing.
    property bool resolved: false

    function ask(info) {
        dialog.resolved = false
        dialog.addonName = info.name
        dialog.addonVersion = info.version
        dialog.host = info.host
        dialog.addonDescription = info.description
        dialog.adult = info.adult
        dialog.p2p = info.p2p
        dialog.configurationRequired = info.configurationRequired
        dialog.configureUrl = info.configureUrl
        dialog.open()
    }

    // A Popup is not an Item and has no Window attached property; Overlay is
    // the supported way to reach the window (see ConfirmDialog).
    anchors.centerIn: Overlay.overlay
    modal: true
    dim: true
    padding: 24
    readonly property real widthBudget: Overlay.overlay && Overlay.overlay.width > 0
        ? Overlay.overlay.width - 48
        : 460
    width: Math.min(460, widthBudget)
    // Escape and click-outside cancel: a dialog the user did not ask for must
    // be dismissible the way every other dialog is.
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    // Escape, click-outside and Cancel all land here; an accepted dialog has
    // already resolved itself.
    onClosed: if (!dialog.resolved) dialog.cancelled()

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
            text: dialog.configurationRequired ? "Configure this addon?" : "Install this addon?"
            color: Theme.text
            font.pixelSize: Theme.fontTitle
            font.bold: true
            wrapMode: Text.WordWrap
        }

        Text {
            width: parent.width
            text: dialog.host + " wants to add an addon to Gravitas."
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.WordWrap
        }

        Rectangle {
            width: parent.width
            height: info.height + 24
            radius: Theme.radius
            color: Theme.bg
            border.width: 1
            border.color: Theme.border

            Column {
                id: info
                anchors.centerIn: parent
                width: parent.width - 24
                spacing: 6

                Text {
                    width: parent.width
                    text: dialog.addonName + (dialog.addonVersion ? "  " + dialog.addonVersion : "")
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.bold: true
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    visible: dialog.addonDescription.length > 0
                    text: dialog.addonDescription
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                    maximumLineCount: 3
                    elide: Text.ElideRight
                }
                Row {
                    spacing: 8
                    visible: dialog.adult || dialog.p2p
                    // The addon's own declarations, shown where the decision is
                    // made rather than buried in a settings screen.
                    AppChip { visible: dialog.adult; text: "Adult content" }
                    AppChip { visible: dialog.p2p; text: "Peer-to-peer" }
                }
            }
        }

        Text {
            width: parent.width
            visible: dialog.configurationRequired
            text: "This addon must be set up on its own website before it can serve anything."
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
                // Installing a configuration-required addon yields one that
                // silently serves nothing, so it is not offered.
                visible: dialog.configurationRequired
                text: "Configure in browser"
                tone: "accent"
                onClicked: {
                    var url = dialog.configureUrl
                    dialog.resolved = true
                    dialog.close()
                    dialog.configureRequested(url)
                }
            }
            AppButton {
                visible: !dialog.configurationRequired
                text: "Install"
                tone: "accent"
                onClicked: {
                    dialog.resolved = true
                    dialog.close()
                    dialog.confirmed()
                }
            }
        }
    }
}
