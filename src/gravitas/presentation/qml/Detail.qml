import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects
import "components"

Item {
    id: detail
    property string mediaType
    property string mediaId
    signal playUrl(string url)
    signal back()

    onMediaIdChanged: if (mediaId.length) detailController.load(mediaType, mediaId)

    // blurred background art + dark scrim for readability
    Image {
        id: bgSrc
        anchors.fill: parent
        source: detailController && detailController.background ? detailController.background : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        visible: false
    }
    FastBlur {
        anchors.fill: parent
        source: bgSrc
        radius: 64
        // fade the art in once it has decoded instead of popping in
        opacity: bgSrc.status === Image.Ready ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
    }
    Rectangle { anchors.fill: parent; color: Qt.rgba(0.078, 0.078, 0.078, 0.86) }

    // floating back button, above the scrolling content. A semi-opaque dark
    // backing keeps it legible over bright or busy background art.
    Item {
        id: backWrap
        z: 10
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 16
        width: backBtn.width
        height: backBtn.height

        Rectangle {
            anchors.fill: parent
            radius: Theme.radius
            color: Qt.rgba(0, 0, 0, 0.55)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.18)
        }

        AppButton {
            id: backBtn
            ghost: true
            iconGlyph: Icons.arrowLeft
            text: "Back"
            onClicked: detail.back()
        }
    }

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: content
            x: 24
            y: 24
            width: parent.width - 48
            spacing: 16

            // title logo art, with a bold-text fallback
            Item {
                width: parent.width
                height: 120
                Image {
                    id: logo
                    anchors.centerIn: parent
                    height: 110
                    fillMode: Image.PreserveAspectFit
                    source: detailController && detailController.logo ? detailController.logo : ""
                    asynchronous: true
                    visible: status === Image.Ready
                }
                Text {
                    anchors.centerIn: parent
                    visible: !logo.visible
                    text: detailController ? detailController.title : ""
                    color: Theme.text
                    font.pixelSize: 32
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            // meta row: runtime · year · rating + IMDb badge
            Row {
                spacing: 24
                Text {
                    visible: text.length > 0
                    text: detailController ? detailController.runtime : ""
                    color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                }
                Text {
                    visible: text.length > 0
                    text: detailController ? detailController.year : ""
                    color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                }
                Row {
                    spacing: 8
                    visible: ratingText.text.length > 0
                    Text {
                        id: ratingText
                        anchors.verticalCenter: parent.verticalCenter
                        text: detailController ? detailController.imdbRating : ""
                        color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: badge.implicitWidth + 12
                        height: 22
                        radius: 4
                        color: "#f5c518"
                        Text {
                            id: badge
                            anchors.centerIn: parent
                            text: "IMDb"
                            color: "#000000"
                            font.pixelSize: Theme.fontSmall
                            font.bold: true
                        }
                    }
                }
            }

            // description
            Text {
                width: parent.width
                text: detailController ? detailController.description : ""
                color: Theme.text
                opacity: 0.9
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontBody
            }

            // genres
            Column {
                width: parent.width
                spacing: 8
                visible: genresRep.count > 0
                Text { text: "GENRES"; color: Theme.textDim; font.pixelSize: Theme.fontSmall; font.bold: true }
                Flow {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        id: genresRep
                        model: detailController ? detailController.genres : []
                        AppChip { text: modelData }
                    }
                }
            }

            // cast
            Column {
                width: parent.width
                spacing: 8
                visible: castRep.count > 0
                Text { text: "CAST"; color: Theme.textDim; font.pixelSize: Theme.fontSmall; font.bold: true }
                Flow {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        id: castRep
                        model: detailController ? detailController.cast : []
                        AppChip { text: modelData }
                    }
                }
            }

            // directors
            Text {
                width: parent.width
                visible: detailController && detailController.directors && detailController.directors.length > 0
                text: (detailController && detailController.directors)
                    ? "Directed by " + detailController.directors.join(", ")
                    : ""
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }

            // sources
            Text { text: "Sources"; color: Theme.text; font.pixelSize: 20 }
            Column {
                width: parent.width
                spacing: 8
                Repeater {
                    model: streamModel
                    StreamRow {
                        width: content.width
                        name: model.name
                        subtitle: model.title
                        onClicked: if (model.url) detail.playUrl(model.url)
                    }
                }
            }
        }
    }
}
