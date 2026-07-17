import QtQuick

Item {
    id: root
    property string title
    property string addonId
    property string type
    property string catalogId
    property var posters
    // The synthetic Continue Watching row: no addon or catalog stands behind
    // it, and its cards name the episode they would resume into.
    property bool continueWatching: false
    signal openDetail(string type, string id)
    signal seeAll(string addonId, string type, string catalogId)

    implicitHeight: header.height + strip.anchors.topMargin + strip.height

    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 28

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: root.title
            color: "white"
            font.pixelSize: 18
            font.bold: true
        }

        Text {
            id: seeAllLabel
            // Synthetic rows (Continue Watching, Trakt) have no catalog to
            // see all of.
            visible: root.catalogId !== ""
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "See All"
            color: seeAllMouse.containsMouse ? "#f0f0f0" : "#9aa0a6"
            font.underline: seeAllMouse.containsMouse
            Behavior on color { ColorAnimation { duration: 120 } }
            MouseArea {
                id: seeAllMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.seeAll(root.addonId, root.type, root.catalogId)
            }
        }
    }

    ListView {
        id: strip
        anchors.top: header.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        height: 300
        orientation: ListView.Horizontal
        spacing: 16
        clip: true
        // Build every card in the row up front (a row is ~20 posters, well
        // inside this), so sideways scrolling reveals loaded art instead of
        // per-card spinners.
        cacheBuffer: 8000
        reuseItems: true
        // inset the content from the clip edges so the first/last card has
        // room to grow on hover without being clipped
        leftMargin: 10
        rightMargin: 10
        model: root.posters
        delegate: PosterCard {
            height: strip.height
            // Trickle the row's posters left to right: ~30ms apart their
            // decoded textures upload across many frames instead of as one
            // burst. Applies once per created delegate; recycled ones load
            // instantly.
            loadDelay: Math.min(index, 20) * 30
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            posterShape: model.posterShape
            mediaType: model.type
            progressFraction: model.progressFraction
            watched: model.watched
            subtitle: model.progressLabel
            showSubtitle: root.continueWatching
            forgetContext: ({
                mediaId: model.id,
                videoId: "",
                type: model.type,
                name: model.name,
                poster: model.poster ? model.poster : "",
                label: ""
            })
            onClicked: root.openDetail(model.type, model.id)
        }

        // Scroll the strip sideways only on a horizontal wheel/trackpad
        // gesture. A vertical wheel is left unaccepted so it bubbles up to
        // the outer rows ListView and scrolls the page — otherwise hovering
        // any strip would swallow page scrolling.
        WheelHandler {
            acceptedModifiers: Qt.NoModifier
            onWheel: (event) => {
                if (Math.abs(event.angleDelta.x) > Math.abs(event.angleDelta.y)) {
                    strip.contentX = Math.max(
                        0,
                        Math.min(
                            Math.max(0, strip.contentWidth - strip.width),
                            strip.contentX - event.angleDelta.x
                        )
                    )
                } else {
                    event.accepted = false
                }
            }
        }
    }

    // Edge fades: cards dissolving into the background say "more this way"
    // without chrome. Siblings of the ListView, NOT children — a Flickable
    // reparents child items into its contentItem, which would scroll the
    // fades away with the cards. Plain Rectangles with no pointer handlers,
    // so hover and clicks pass straight through to the cards beneath; each
    // side appears only while something is actually hidden behind it.
    Rectangle {
        anchors.left: strip.left
        anchors.top: strip.top
        anchors.bottom: strip.bottom
        width: 56
        opacity: strip.contentWidth > strip.width && !strip.atXBeginning ? 1 : 0
        visible: opacity > 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Theme.bg }
            GradientStop { position: 1.0; color: Qt.rgba(Theme.bg.r, Theme.bg.g, Theme.bg.b, 0) }
        }
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
    }
    Rectangle {
        anchors.right: strip.right
        anchors.top: strip.top
        anchors.bottom: strip.bottom
        width: 56
        opacity: strip.contentWidth > strip.width && !strip.atXEnd ? 1 : 0
        visible: opacity > 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Qt.rgba(Theme.bg.r, Theme.bg.g, Theme.bg.b, 0) }
            GradientStop { position: 1.0; color: Theme.bg }
        }
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
    }
}
