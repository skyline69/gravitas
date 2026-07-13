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

        // discoverController is a context property; while StackView tears this
        // page down on Back, queued binding re-evaluations can momentarily see
        // it as null, so every reference is null-guarded.
        AppComboBox {
            id: typeBox
            width: 160
            model: discoverController ? discoverController.typeOptions : []
            currentIndex: discoverController ? discoverController.typeIndex : 0
            onActivated: (index) => { if (discoverController) discoverController.selectType(index) }
        }
        AppComboBox {
            id: catalogBox
            width: 220
            model: discoverController ? discoverController.catalogOptions : []
            currentIndex: discoverController ? discoverController.catalogIndex : 0
            onActivated: (index) => { if (discoverController) discoverController.selectCatalog(index) }
        }
        AppComboBox {
            id: genreBox
            width: 200
            model: discoverController ? discoverController.genreOptions : []
            currentIndex: discoverController ? discoverController.genreIndex : 0
            onActivated: (index) => { if (discoverController) discoverController.selectGenre(index) }
        }
    }

    GridView {
        id: grid
        maximumFlickVelocity: 12000
        flickDeceleration: 8000
        anchors.top: filters.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        anchors.rightMargin: 0
        cellWidth: 180
        cellHeight: 300
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        model: discoverModel
        delegate: PosterCard {
            // fill the whole cell and centre the poster inside it, so the
            // hover scale-up grows into the cell's slack instead of past the
            // grid's clip edge (fixes edge-column/row clipping)
            width: 180
            height: 300
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            mediaType: model.type
            onClicked: root.openDetail(model.type, model.id)
        }
        onAtYEndChanged: if (atYEnd && discoverController) discoverController.loadMore()
    }

    Item {
        anchors.fill: grid
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: (w) => Scroll.wheel(grid, w)
        }
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
