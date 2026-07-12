import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    signal openDetail(string type, string id)

    Rectangle {
        id: addonBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 56
        color: "#1c1c1c"

        Row {
            anchors.fill: parent
            anchors.margins: 12
            spacing: 8

            TextField {
                id: urlField
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width - addButton.width - parent.spacing
                placeholderText: "Addon manifest URL…"
            }

            Button {
                id: addButton
                anchors.verticalCenter: parent.verticalCenter
                text: "Add"
                onClicked: {
                    addonController.addAddon(urlField.text)
                    urlField.text = ""
                }
            }
        }
    }

    GridView {
        anchors.top: addonBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        cellWidth: 180; cellHeight: 280
        model: posterModel
        delegate: PosterCard {
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            onClicked: home.openDetail(model.type, model.id)
        }
    }

    BusyIndicator {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: catalogController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
}
