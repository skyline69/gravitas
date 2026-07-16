import QtQuick
import Qt5Compat.GraphicalEffects
import "."

Item {
    id: root
    property string title
    property string posterUrl
    property string mediaType: "" // "movie" | "series" — picks the filler icon
    property real progressFraction: 0
    property bool watched: false
    // One line under the title ("S1E3 · Pilot"). `showSubtitle` reserves the
    // line for the whole row — see the note on subtitleLabel below.
    property string subtitle: ""
    property bool showSubtitle: false
    // { mediaId, videoId, type, name, poster, label } — null disables the menu.
    property var forgetContext: null
    signal clicked()
    width: 160
    height: 260

    function openMenu(position) {
        if (!root.forgetContext)
            return
        var items = []
        // Offered for a series too. The grid cannot infer that a show is
        // finished -- it has no episode list -- but it does not need to: the
        // user is saying so, and the repository finishes the episodes it knows
        // were started, so the show badges and leaves Continue Watching rather
        // than keeping a bar. Marking a single EPISODE watched still lives on
        // the Detail page, where the episodes are.
        if (!root.watched) {
            items.push({
                label: "Mark as watched",
                action: () => progressController.markWatched(root.forgetContext)
            })
        }
        if (root.progressFraction > 0 || root.watched) {
            items.push({
                label: "Forget progress",
                action: () => progressController.forgetMedia(root.forgetContext.mediaId)
            })
        }
        if (items.length === 0)
            return
        cardMenu.entries = items
        cardMenu.popupAt(root, position)
    }

    ContextMenu { id: cardMenu }

    // lift the hovered card above its neighbours so the scaled-up poster
    // overlaps them instead of being drawn underneath
    z: mouse.containsMouse ? 2 : 0

    // Content is centred (not top-anchored) so the row/grid can be a little
    // taller than the poster, leaving vertical slack for the hover scale-up
    // to grow into without being clipped by the surrounding view.
    Column {
        id: content
        spacing: 6
        anchors.centerIn: parent

        // scale the whole card (poster + title) as one unit on hover so the
        // gap between them is preserved; shrink slightly on press
        transformOrigin: Item.Center
        scale: mouse.pressed ? 0.95 : (mouse.containsMouse ? 1.06 : 1.0)
        Behavior on scale { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }

        Item {
            id: cover
            width: 160; height: 220

            // skeleton placeholder shown until the poster is ready: a surface
            // fill with an animated shimmer sweep while the image loads
            // Filler for missing/broken posters: a surface panel with a media
            // glyph (film for movies, tv for series).
            Rectangle {
                anchors.fill: parent
                radius: 14
                color: Theme.surface
                visible: !root.posterUrl || img.status === Image.Error
                AppIcon {
                    anchors.centerIn: parent
                    glyph: root.mediaType === "series" ? Icons.liveTv : Icons.theaters
                    font.pixelSize: 44
                    color: Theme.borderStrong
                }
            }

            Rectangle {
                id: skeleton
                anchors.fill: parent
                radius: 14
                color: Theme.surface
                clip: true
                visible: img.status === Image.Loading

                Rectangle {
                    id: shimmer
                    height: parent.height * 2
                    width: parent.width * 0.55
                    y: -parent.height / 2
                    rotation: 18
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: "transparent" }
                        GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.07) }
                        GradientStop { position: 1.0; color: "transparent" }
                    }
                    SequentialAnimation on x {
                        loops: Animation.Infinite
                        running: img.status === Image.Loading
                        NumberAnimation {
                            from: -skeleton.width * 0.6
                            to: skeleton.width * 1.2
                            duration: 1100
                            easing.type: Easing.InOutQuad
                        }
                        PauseAnimation { duration: 350 }
                    }
                }
            }

            // the poster, masked to a rounded rectangle
            Image {
                id: img
                anchors.fill: parent
                // Request/decode a poster sized for this card, not full-res art.
                source: Img.sized(root.posterUrl, 220)
                sourceSize.width: 220
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: true
                visible: false
            }
            Rectangle {
                id: mask
                anchors.fill: parent
                radius: 14
                visible: false
            }
            OpacityMask {
                anchors.fill: parent
                source: img
                maskSource: mask
                // fade the poster in once it has decoded
                opacity: img.status === Image.Ready ? 1.0 : 0.0
                Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
            }

            // rounded white frame — only on the active (hovered) poster
            Rectangle {
                anchors.fill: parent
                radius: 14
                color: "transparent"
                border.width: 2
                border.color: "white"
                visible: mouse.containsMouse
            }

            // Resume bar across the poster's bottom edge, inside the rounding.
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 6
                height: 4
                radius: 2
                color: Qt.rgba(0, 0, 0, 0.55)
                visible: root.progressFraction > 0 && !root.watched
                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: parent.width * Math.max(0, Math.min(1, root.progressFraction))
                    radius: 2
                    color: Theme.accent
                    Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
                }
            }

            Rectangle {
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.margins: 8
                width: 24; height: 24; radius: 12
                color: Qt.rgba(0, 0, 0, 0.6)
                visible: root.watched
                AppIcon {
                    anchors.centerIn: parent
                    glyph: Icons.check
                    font.pixelSize: 15
                    color: Theme.positive
                }
            }

            MouseArea {
                id: mouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onClicked: (event) => {
                    if (event.button === Qt.RightButton)
                        root.openMenu(Qt.point(event.x, event.y))
                    else
                        root.clicked()
                }
                onPressAndHold: (event) => root.openMenu(Qt.point(event.x, event.y))
            }
        }

        Text {
            id: label
            width: 160
            // Reserve a constant two-line height so a wrapping (2-line) title
            // doesn't make the centred Column taller and shove the poster up.
            // Short titles top-align in this fixed box.
            height: 2 * (fontMetrics.height)
            verticalAlignment: Text.AlignTop
            text: root.title
            // brighten dim -> full on hover (scales with the card as one unit)
            color: mouse.containsMouse ? Theme.text : Theme.textDim
            elide: Text.ElideRight
            maximumLineCount: 2
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Behavior on color { ColorAnimation { duration: Theme.durMed } }

            FontMetrics { id: fontMetrics; font: label.font }
        }

        // Reserved by the row, not by the card: within one row some cards
        // carry a subtitle (a series names its episode) and some do not (a
        // movie has nothing to add). Sizing this per-card would leave the
        // subtitled posters sitting higher than their neighbours, because the
        // Column is centred in the card. So the whole row reserves the line or
        // none of it does.
        Text {
            id: subtitleLabel
            visible: root.showSubtitle
            width: 160
            height: visible ? subtitleMetrics.height : 0
            text: root.subtitle
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            maximumLineCount: 1
            horizontalAlignment: Text.AlignHCenter

            FontMetrics { id: subtitleMetrics; font: subtitleLabel.font }
        }
    }
}
