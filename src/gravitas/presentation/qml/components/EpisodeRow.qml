import QtQuick
import Qt5Compat.GraphicalEffects
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
    signal clicked()

    height: 106
    radius: Theme.radius
    color: root.active
        ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.10)
        : (hover.hovered ? Theme.surfaceHover : Theme.surface)
    border.width: root.active ? 2 : 1
    border.color: root.active ? Theme.accent : Theme.border
    Behavior on color { ColorAnimation { duration: Theme.durFast } }
    Behavior on border.color { ColorAnimation { duration: Theme.durFast } }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.clicked() }

    Row {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 14

        Item {
            id: thumb
            width: 160
            height: 90
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
                visible: false
            }
            Rectangle { id: thumbMask; anchors.fill: parent; radius: Theme.radiusSmall; visible: false }
            OpacityMask {
                anchors.fill: parent
                source: img
                maskSource: thumbMask
                opacity: img.status === Image.Ready ? 1.0 : 0.0
                Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
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
                    text: "E" + root.episodeNumber
                    color: root.active ? Theme.accentHover : Theme.accent
                    font.pixelSize: Theme.fontBody
                    font.bold: true
                }
                Text {
                    width: parent.width - 90
                    text: root.title
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.bold: true
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }
            }
            Text {
                visible: text.length > 0
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
}
