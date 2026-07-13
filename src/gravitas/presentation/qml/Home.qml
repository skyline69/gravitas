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
        maximumFlickVelocity: 12000
        flickDeceleration: 8000
        anchors.fill: parent
        anchors.leftMargin: 24
        // Reach the window edge so the scrollbar hugs it; delegates keep a
        // right inset (see delegate width) so content isn't under the bar.
        anchors.rightMargin: 0
        anchors.bottomMargin: 24
        // Reserve space for the floating bar (top margin 12 + height 56 + gap);
        // rows still scroll up underneath it and are hidden by the opaque bar.
        topMargin: 80
        spacing: 28
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        model: catalogRowsModel
        delegate: CatalogRowStrip {
            width: rowsView.width - 24
            title: model.title
            addonId: model.addonId
            type: model.type
            catalogId: model.catalogId
            posters: model.posters
            onOpenDetail: (t, id) => home.openDetail(t, id)
            onSeeAll: (aid, t, cid) => home.seeAll(aid, t, cid)
        }
    }

    // Faster mouse-wheel scrolling. A plain Item + WheelHandler overlay (not a
    // MouseArea, so it doesn't hijack the cursor/hover of the posters below);
    // sitting above the view, it receives the wheel where a WheelHandler inside
    // the Flickable would not.
    Item {
        anchors.fill: rowsView
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: (w) => Scroll.wheel(rowsView, w)
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
