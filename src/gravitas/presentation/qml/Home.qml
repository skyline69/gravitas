import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    signal openDetail(string type, string id)
    signal seeAll(string type, string catalogId)

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

    ListView {
        id: rowsView
        anchors.top: addonBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        spacing: 28
        clip: true
        model: catalogRowsModel
        delegate: CatalogRowStrip {
            width: rowsView.width
            title: model.title
            type: model.type
            catalogId: model.catalogId
            posters: model.posters
            onOpenDetail: (t, id) => home.openDetail(t, id)
            onSeeAll: (t, cid) => home.seeAll(t, cid)
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
