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
    // Where the OUTER (vertical) view's clip cuts this row, in this row's
    // coordinates — forwarded to every card so posters feather into the
    // page's top/bottom edges. Defaults park them far away.
    property real viewClipTop: -100000
    property real viewClipBottom: 100000
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
        // Sideways wheel/trackpad scrolling that doesn't eat the click after
        // it (see the component).
        WheelScroller { flick: strip; horizontal: true }
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
            // Edge dissolve: cards fade out as they approach a scrolled
            // edge, which says "more this way" over ANY background — a
            // bg-coloured gradient painted on top (the previous approach)
            // showed as a dark slab once the ambient glow made the
            // background non-flat. Continuous in contentX, so cards ghost
            // in and out smoothly while the strip moves.
            opacity: {
                var fadeZone = 140
                var left = x - strip.contentX + width / 2
                var right = strip.width - left
                var f = 1.0
                if (!strip.atXBeginning)
                    f = Math.min(f, Math.max(0.15, left / fadeZone))
                if (!strip.atXEnd)
                    f = Math.min(f, Math.max(0.15, right / fadeZone))
                return f
            }
            // Eases the dissolve's appearance when scrolling starts/stops —
            // the atXBeginning/atXEnd flips above are instant otherwise.
            Behavior on opacity { NumberAnimation { duration: Theme.durMed * 2 } }
            // Feather the actual cuts: the poster's pixels dissolve into
            // the clip edges instead of being sliced by them. Horizontal
            // edges always carry the true geometry; their visibility is a
            // separate animated strength, so the feather fades in place
            // rather than popping (or sweeping, if the position animated).
            edgeClipLeft: strip.contentX - x
            edgeClipRight: strip.contentX + strip.width - x
            edgeFadeLeftStrength: strip.atXBeginning ? 0 : 1
            edgeFadeRightStrength: strip.atXEnd ? 0 : 1
            Behavior on edgeFadeLeftStrength { NumberAnimation { duration: Theme.durMed * 2 } }
            Behavior on edgeFadeRightStrength { NumberAnimation { duration: Theme.durMed * 2 } }
            // Vertical: the page view's edges, handed down by Home. No
            // gate needed — a fully visible card's clip edge lies beyond
            // the feather zone by construction.
            edgeClipTop: root.viewClipTop - strip.y
            edgeClipBottom: root.viewClipBottom - strip.y
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
}
