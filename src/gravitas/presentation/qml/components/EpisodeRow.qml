import QtQuick
import Gravitas
import "."

// One episode in the Detail page's episode list: 16:9 thumbnail, number +
// title, air date, two-line overview. Highlights when selected.
Rectangle {
    id: root
    property string title
    property string thumbnailUrl
    property int seasonNumber
    property int episodeNumber
    property string overview
    property string released
    property bool active: false
    property real progressFraction: 0
    property bool watched: false
    // The still's width; its height and the row's follow at 16:9. Detail's
    // list uses the default; the player's episode panel is narrower.
    property int thumbWidth: 160
    readonly property int thumbHeight: Math.round(root.thumbWidth * 9 / 16)
    // The air date line, which the panel leaves out to keep its rows short.
    property bool showReleased: true
    // { mediaId, videoId, type, name, poster, label } — null disables the menu.
    property var forgetContext: null
    signal clicked()
    // The pointer has rested on this row long enough to suggest a click is
    // coming: the page starts fetching its sources now, so the Sources page
    // opens on a request already seconds under way. The dwell keeps a pointer
    // sweeping down the list from firing a request per row it crosses.
    signal intent()

    height: root.thumbHeight + 16
    radius: Theme.radius
    color: root.active
        ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.10)
        : (hover.hovered ? Theme.surfaceHover : Theme.surface)
    border.width: root.active ? 2 : 1
    border.color: root.active ? Theme.accent : Theme.border
    Behavior on color { ColorAnimation { duration: Theme.durFast } }
    Behavior on border.color { ColorAnimation { duration: Theme.durFast } }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    Timer {
        interval: 250
        running: hover.hovered
        onTriggered: root.intent()
    }
    TapHandler { onTapped: root.clicked() }
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: (event) => root.openMenu(event.position)
    }
    TapHandler {
        // Touch/trackpad equivalent of a right-click.
        acceptedDevices: PointerDevice.TouchScreen
        onLongPressed: root.openMenu(point.position)
    }

    function openMenu(position) {
        if (!root.forgetContext)
            return
        var items = []
        if (!root.watched) {
            items.push({
                label: "Mark as watched",
                action: () => ProgressController.markWatched(root.forgetContext)
            })
        }
        if (root.progressFraction > 0 || root.watched) {
            items.push({
                label: "Forget progress",
                action: () => ProgressController.forget(
                    root.forgetContext.mediaId, root.forgetContext.videoId)
            })
        }
        if (items.length === 0)
            return
        root.showMenu(items, position)
    }

    // Built on first right-click, not with every card. Eagerly instantiating a
    // ContextMenu costs ~36 KB per delegate (measured), paid by every visible
    // card for a menu most are never asked for.
    Loader {
        id: menuLoader
        active: false
        sourceComponent: ContextMenu { }
    }

    function showMenu(items, position) {
        menuLoader.active = true
        // Loader.item is typed QObject; the assertion is what lets the menu's
        // own members be checked.
        const menu = menuLoader.item as ContextMenu
        menu.entries = items
        menu.popupAt(root, position)
    }

    Row {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 14

        Item {
            id: thumb
            width: root.thumbWidth
            height: root.thumbHeight
            anchors.verticalCenter: parent.verticalCenter

            // Filler for missing/broken thumbnails.
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: Theme.surfaceHover
                visible: !root.thumbnailUrl || img.status === Image.Error
                AppIcon {
                    anchors.centerIn: parent
                    glyph: Icons.liveTv
                    font.pixelSize: 28
                    color: Theme.borderStrong
                }
            }
            // Pulse while the thumbnail decodes.
            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSmall
                color: Theme.surfaceHover
                visible: img.status === Image.Loading
                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    running: img.status === Image.Loading
                    NumberAnimation { from: 0.5; to: 1.0; duration: 700; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 1.0; to: 0.5; duration: 700; easing.type: Easing.InOutQuad }
                }
            }
            Image {
                id: img
                anchors.fill: parent
                // Episode stills are small: cap the decode size so a long
                // list doesn't hold full-res frames in memory.
                source: Img.sized(root.thumbnailUrl, 320)
                sourceSize.width: 320
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: true
                opacity: status === Image.Ready ? 1.0 : 0.0
                Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
            }
            // Rounded-corner window instead of an OpacityMask: a border ring
            // in the row's own colour, drawn over the still's edges. Its
            // inner edge is the rounded corner; outside its own rounded
            // outer edge the row background shows anyway, so nothing
            // mismatches — and the colour binding follows hover/active
            // tints. Rectangles batch; a per-row ShaderEffect does not, and
            // a long season paid one per episode.
            Rectangle {
                anchors.fill: parent
                anchors.margins: -6
                radius: Theme.radiusSmall + 6
                color: "transparent"
                border.width: 6
                // Tracks root.color directly — that binding already animates
                // through the row's own colour Behavior.
                border.color: root.color
            }
        }

        Column {
            width: parent.width - thumb.width - parent.spacing - 8
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4

            Row {
                width: parent.width
                spacing: 8
                Text {
                    id: episodeLabel
                    text: "E" + root.episodeNumber
                    color: root.active ? Theme.accentHover : Theme.accent
                    font.pixelSize: Theme.fontBody
                    font.bold: true
                }
                Text {
                    // What the number and the check leave, not a fixed 90px:
                    // in the player's narrow panel that guess cost the title
                    // a third of its room.
                    width: parent.width - episodeLabel.width - parent.spacing
                        - (root.watched ? 16 + parent.spacing : 0)
                    text: root.title
                    color: root.watched ? Theme.textDim : Theme.text
                    font.pixelSize: Theme.fontBody
                    font.bold: true
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }
                AppIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.watched
                    glyph: Icons.check
                    font.pixelSize: 16
                    color: Theme.positive
                }
            }
            Text {
                visible: root.showReleased && text.length > 0
                text: root.released
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: root.overview
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.WordWrap
                elide: Text.ElideRight
                maximumLineCount: 2
            }
        }
    }

    // Resume bar, pinned to the row's bottom edge. Inset horizontally by the
    // corner radius so it starts where the straight edge does — a 2px inset
    // left it visibly poking past the corner arc.
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: root.radius
        anchors.rightMargin: root.radius
        anchors.bottomMargin: 3
        height: 3
        radius: 1.5
        color: Theme.border
        visible: root.progressFraction > 0 && !root.watched
        Rectangle {
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: parent.width * Math.max(0, Math.min(1, root.progressFraction))
            radius: 1.5
            color: Theme.accent
            Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
        }
    }
}
