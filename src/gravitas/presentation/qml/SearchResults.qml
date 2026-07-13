import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: root
    objectName: "searchResultsPage"
    signal openDetail(string type, string id)
    signal back()

    GridView {
        id: grid
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.bottomMargin: 24
        topMargin: 80
        cellWidth: 180
        cellHeight: 300
        clip: true
        model: searchPageModel
        delegate: PosterCard {
            width: 180
            height: 300
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            mediaType: model.type
            onClicked: root.openDetail(model.type, model.mediaId)
        }
    }
}
