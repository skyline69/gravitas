import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: root
    signal openDetail(string type, string id)
    signal back()

    Row {
        id: filters
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 12
        height: 44
        spacing: 8

        AppButton { ghost: true; iconGlyph: Icons.arrowLeft; text: "Back"; onClicked: root.back() }

        AppComboBox {
            id: typeBox
            width: 160
            model: discoverController.typeOptions
            currentIndex: discoverController.typeIndex
            onActivated: (index) => discoverController.selectType(index)
        }
        AppComboBox {
            id: catalogBox
            width: 220
            model: discoverController.catalogOptions
            currentIndex: discoverController.catalogIndex
            onActivated: (index) => discoverController.selectCatalog(index)
        }
        AppComboBox {
            id: genreBox
            width: 200
            model: discoverController.genreOptions
            currentIndex: discoverController.genreIndex
            onActivated: (index) => discoverController.selectGenre(index)
        }
    }

    GridView {
        id: grid
        anchors.top: filters.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        cellWidth: 180
        cellHeight: 300
        clip: true
        model: discoverModel
        delegate: PosterCard {
            width: 160
            height: 300
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            onClicked: root.openDetail(model.type, model.id)
        }
        onAtYEndChanged: if (atYEnd) discoverController.loadMore()
    }

    AppSpinner {
        id: busy
        anchors.centerIn: parent
        running: false
        Connections {
            target: discoverController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
}
