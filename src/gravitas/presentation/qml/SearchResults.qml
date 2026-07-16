import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: root
    objectName: "searchResultsPage"
    signal openDetail(string type, string id)
    signal back()

    property bool loading: false
    Connections {
        target: searchController
        function onPageLoadingChanged(l) { root.loading = l }
    }

    Row {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.topMargin: 88 // clear the floating bar
        anchors.leftMargin: 24
        spacing: 12
        AppButton {
            ghost: true
            iconGlyph: Icons.arrowLeft
            tooltip: "Back"
            onClicked: root.back()
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: 'Search results for "' + (searchController ? searchController.query : "") + '"'
            color: Theme.text
            font.pixelSize: Theme.fontTitle
            elide: Text.ElideRight
            width: root.width - 24 - x
        }
    }

    GridView {
        id: grid
        maximumFlickVelocity: 12000
        flickDeceleration: 8000
        anchors.top: header.bottom
        anchors.topMargin: 16
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 24
        anchors.rightMargin: 0
        anchors.bottomMargin: 24
        cellWidth: 180
        cellHeight: 300
        clip: true
        visible: !root.loading
        ScrollBar.vertical: AppScrollBar {}
        model: searchPageModel
        delegate: PosterCard {
            width: 180
            height: 300
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            mediaType: model.type
            progressFraction: model.progressFraction
            watched: model.watched
            forgetContext: ({
                mediaId: model.mediaId,
                videoId: "",
                type: model.type,
                name: model.name,
                poster: model.poster ? model.poster : "",
                label: ""
            })
            onClicked: root.openDetail(model.type, model.mediaId)
        }
    }

    Item {
        anchors.fill: grid
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: (w) => Scroll.wheel(grid, w)
        }
    }

    // Skeleton grid shown while a submitted search is loading: pulsing poster
    // placeholders in a staggered wave.
    Flow {
        anchors.fill: grid
        clip: true
        visible: root.loading
        Repeater {
            model: root.loading ? 18 : 0
            delegate: Item {
                required property int index
                width: 180
                height: 300
                SequentialAnimation on opacity {
                    running: root.loading
                    loops: Animation.Infinite
                    PauseAnimation { duration: (index % 6) * 90 }
                    NumberAnimation { from: 0.35; to: 0.8; duration: 650; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 0.8; to: 0.35; duration: 650; easing.type: Easing.InOutQuad }
                }
                Column {
                    anchors.centerIn: parent
                    spacing: 8
                    Rectangle { width: 160; height: 220; radius: 14; color: Theme.surfaceHover }
                    Rectangle {
                        width: 120
                        height: 12
                        radius: 4
                        color: Theme.surfaceHover
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                }
            }
        }
    }
}
