import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    signal openDetail(string type, string id)

    GridView {
        anchors.fill: parent
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
