import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: settings
    objectName: "settingsPage"
    signal back()

    Column {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 24
        spacing: 20

        Row {
            spacing: 12
            AppButton {
                ghost: true
                iconGlyph: Icons.arrowLeft
                onClicked: settings.back()
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Settings"
                color: Theme.text
                font.pixelSize: Theme.fontTitle
            }
        }

        // --- Addons section ---
        Text {
            text: "Addons"
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
        Row {
            width: parent.width
            spacing: 8
            AppTextField {
                id: urlField
                width: parent.width - addButton.width - parent.spacing
                placeholderText: "Addon manifest URL…"
            }
            AppButton {
                id: addButton
                text: "Add"
                onClicked: {
                    addonController.addAddon(urlField.text)
                    urlField.text = ""
                }
            }
        }
        ListView {
            width: parent.width
            height: Math.min(contentHeight, 300)
            model: addonListModel
            interactive: true
            clip: true
            spacing: 4
            delegate: Rectangle {
                width: ListView.view.width
                height: 44
                radius: Theme.radius
                color: Theme.surface
                required property string name
                required property string addonId
                required property bool removable
                Text {
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: parent.name
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                }
                AppButton {
                    ghost: true
                    iconGlyph: Icons.trash
                    visible: parent.removable
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: settingsController.removeAddon(parent.addonId)
                }
            }
        }

        // --- About section ---
        Text {
            text: "About"
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
        Column {
            spacing: 4
            Text {
                text: "Gravitas"
                color: Theme.text
                font.pixelSize: Theme.fontBody
            }
            Text {
                text: "A memory-efficient, Linux-first media center."
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
            Text {
                text: "github.com/…/gravitas"
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
        }
    }
}
