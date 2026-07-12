import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: detail
    property string mediaType
    property string mediaId
    signal playUrl(string url)

    onMediaIdChanged: if (mediaId.length) detailController.load(mediaType, mediaId)

    property string title: ""
    property string description: ""
    Connections {
        target: detailController
        function onTitleChanged(t) { detail.title = t }
        function onDescriptionChanged(d) { detail.description = d }
    }

    Column {
        anchors.fill: parent; anchors.margins: 24; spacing: 12
        Text { text: detail.title; color: Theme.text; font.pixelSize: 28; font.bold: true }
        Text {
            text: detail.description; color: Theme.textDim; width: parent.width
            wrapMode: Text.WordWrap
        }
        Text { text: "Sources"; color: Theme.text; font.pixelSize: 20 }
        ListView {
            width: parent.width; height: 300; spacing: 8
            model: streamModel
            delegate: StreamRow {
                width: ListView.view.width
                name: model.name
                subtitle: model.title
                onClicked: if (model.url) detail.playUrl(model.url)
            }
        }
    }
}
