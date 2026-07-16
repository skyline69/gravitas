import QtQuick
import QtQuick.Controls
import "components"

// Stream picker for one episode, pushed from the series Detail page.
Item {
    id: sources
    objectName: "sourcesPage"
    signal playUrl(string url, var headers)
    signal back()

    Rectangle { anchors.fill: parent; color: Theme.bg }

    Column {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 24
        spacing: 16

        Row {
            spacing: 12
            BackButton {
                anchors.verticalCenter: parent.verticalCenter
                onClicked: sources.back()
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: detailController ? detailController.sourcesLabel : "Sources"
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.bold: true
                elide: Text.ElideRight
            }
            AppSpinner {
                anchors.verticalCenter: parent.verticalCenter
                width: 18; height: 18
                running: detailController ? detailController.streamsLoading : false
            }
        }

        Text {
            visible: list.count === 0
                && !(detailController && detailController.streamsLoading)
            text: "No sources available. Add a streaming addon to see sources."
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
        }
    }

    // Skeleton rows while the resolve is in flight and nothing has arrived —
    // the page reads as "loading sources", not empty-then-sudden-list.
    Column {
        anchors.top: header.bottom
        anchors.topMargin: 16
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        spacing: 8
        visible: list.count === 0
            && detailController && detailController.streamsLoading
        Repeater {
            model: 6
            StreamRowSkeleton {
                required property int index
                width: parent.width
                pulseDelay: index * 90
            }
        }
    }

    ListView {
        id: list
        anchors.top: header.bottom
        anchors.topMargin: 16
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.bottomMargin: 24
        spacing: 8
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: AppScrollBar {}
        model: streamModel
        delegate: StreamRow {
            width: list.width
            name: model.name
            subtitle: model.subtitle
            resolution: model.resolution
            instant: model.instant
            tags: model.tags
            stars: model.stars
            detailText: model.extra
            onClicked: {
                if (model.external) {
                    playerController.openExternal(model.external)
                    return
                }
                if (!model.url)
                    return
                // Identity must land before play(); the controller
                // reads it to resume and to record.
                playerController.setMediaContext(detailController.mediaContext())
                sources.playUrl(model.url, model.headers)
            }
        }
    }
}
