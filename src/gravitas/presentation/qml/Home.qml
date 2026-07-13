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
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.bottomMargin: 24
        // Reserve space for the floating bar (top margin 12 + height 56 + gap);
        // rows still scroll up underneath it and are hidden by the opaque bar.
        topMargin: 80
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
