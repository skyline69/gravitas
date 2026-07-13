import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    objectName: "homePage"
    signal openDetail(string type, string id)
    signal seeAll(string addonId, string type, string catalogId)

    ListView {
        id: rowsView
        anchors.top: parent.top
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
            addonId: model.addonId
            type: model.type
            catalogId: model.catalogId
            posters: model.posters
            onOpenDetail: (t, id) => home.openDetail(t, id)
            onSeeAll: (aid, t, cid) => home.seeAll(aid, t, cid)
        }
    }

    AppSpinner {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: catalogController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
}
