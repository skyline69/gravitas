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
    signal openSources()

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

    // floating back button, above the scrolling content
    BackButton {
        z: 10
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 16
        blurTarget: flick
        onClicked: detail.back()
    }

    Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: content.implicitHeight + 48
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: AppScrollBar {}

        Column {
            id: content
            x: 24
            y: 24
            width: parent.width - 48
            spacing: 16

            // title logo art with a bold-text fallback: the title shows while
            // the logo loads, then the two crossfade (title stays if no logo)
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
                    opacity: status === Image.Ready ? 1.0 : 0.0
                    Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                }
                Text {
                    anchors.centerIn: parent
                    text: detailController ? detailController.title : ""
                    color: Theme.text
                    font.pixelSize: 32
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    opacity: logo.status === Image.Ready ? 0.0 : 1.0
                    Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
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

            // episodes (series with videos only)
            Column {
                width: parent.width
                spacing: 12
                visible: detail.mediaType === "series" && episodesRep.count > 0

                Item {
                    width: parent.width
                    height: seasonBox.height
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Episodes"
                        color: Theme.text
                        font.pixelSize: 20
                    }
                    AppComboBox {
                        id: seasonBox
                        anchors.right: parent.right
                        model: detailController ? detailController.seasonOptions : []
                        currentIndex: detailController ? detailController.seasonIndex : 0
                        onActivated: (index) => { if (detailController) detailController.selectSeason(index) }
                    }
                }

                Column {
                    width: parent.width
                    spacing: 8
                    Repeater {
                        id: episodesRep
                        model: episodeModel
                        EpisodeRow {
                            width: content.width
                            title: model.title
                            thumbnailUrl: model.thumbnail
                            seasonNumber: model.season
                            episodeNumber: model.episode
                            overview: model.overview
                            released: model.released
                            active: detailController
                                && detailController.selectedEpisodeId === model.videoId
                            progressFraction: model.progressFraction
                            watched: model.watched
                            forgetContext: ({
                                mediaId: detail.mediaId,
                                videoId: model.videoId,
                                type: "series",
                                name: detailController ? detailController.title : "",
                                poster: detailController ? detailController.poster : "",
                                label: "S" + model.season + "E" + model.episode
                                    + (model.title ? " · " + model.title : "")
                            })
                            onClicked: {
                                detailController.selectEpisode(
                                    model.videoId, model.season, model.episode, model.title)
                                detail.openSources()
                            }
                        }
                    }
                }
            }

            // sources — inline for movies only; episode sources open on their
            // own page (pushed when an episode row is clicked)
            readonly property bool inlineSources:
                !(detail.mediaType === "series" && episodesRep.count > 0)
            Row {
                spacing: 12
                visible: content.inlineSources
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Sources"
                    color: Theme.text
                    font.pixelSize: 20
                }
                AppSpinner {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 18; height: 18
                    running: detailController ? detailController.streamsLoading : false
                }
            }
            Column {
                width: parent.width
                spacing: 8
                visible: content.inlineSources
                Text {
                    visible: sourcesRep.count === 0
                        && !(detailController && detailController.streamsLoading)
                    text: "No sources available. Add a streaming addon to see sources."
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }
                Repeater {
                    model: (sourcesRep.count === 0
                        && detailController && detailController.streamsLoading) ? 4 : 0
                    StreamRowSkeleton {
                        required property int index
                        width: content.width
                        pulseDelay: index * 90
                    }
                }
                Repeater {
                    id: sourcesRep
                    model: streamModel
                    StreamRow {
                        width: content.width
                        name: model.name
                        subtitle: model.subtitle
                        resolution: model.resolution
                        instant: model.instant
                        tags: model.tags
                        stars: model.stars
                        detailText: model.extra
                        onClicked: if (model.url) detail.playUrl(model.url)
                    }
                }
            }
        }
    }
}
