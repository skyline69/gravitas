import QtQuick
import "."

// One playable source. Structured fields (resolution/instant/tags/stars)
// render as chips; free-form leftovers go on the dim lines. Falls back to a
// plain name row when the label had no recognisable structure.
Rectangle {
    id: root
    property string name
    property string subtitle
    property string resolution: ""
    property bool instant: false
    property var tags: []
    property int stars: 0
    // Named detailText, not `detail`: a property named `detail` shadows the
    // Detail page id inside delegates and breaks `detail.playUrl(...)`.
    property string detailText: ""
    signal clicked()

    readonly property bool structured: resolution.length > 0 || instant
        || tags.length > 0 || stars > 0

    height: 64
    radius: Theme.radiusSmall
    color: mouse.containsMouse ? Theme.surfaceHover : Theme.surface
    Behavior on color { ColorAnimation { duration: Theme.durFast } }

    Row {
        anchors.left: parent.left
        anchors.right: playIcon.left
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 12

        // Resolution badge.
        Rectangle {
            visible: root.resolution.length > 0
            anchors.verticalCenter: parent.verticalCenter
            width: resText.implicitWidth + 16
            height: 26
            radius: Theme.radiusSmall
            color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
            Text {
                id: resText
                anchors.centerIn: parent
                text: root.resolution
                color: Theme.accentHover
                font.pixelSize: Theme.fontSmall
                font.bold: true
            }
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3

            Row {
                spacing: 6

                // Fallback: raw name when nothing structured was recognised.
                Text {
                    visible: !root.structured
                    text: root.name
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: Theme.fontBody
                    elide: Text.ElideRight
                    maximumLineCount: 1
                }

                Rectangle {
                    visible: root.instant
                    anchors.verticalCenter: parent.verticalCenter
                    width: instantText.implicitWidth + 14
                    height: 20
                    radius: 10
                    color: Qt.rgba(Theme.positive.r, Theme.positive.g, Theme.positive.b, 0.16)
                    Text {
                        id: instantText
                        anchors.centerIn: parent
                        text: "Instant"
                        color: Theme.positive
                        font.pixelSize: Theme.fontSmall
                    }
                }
                Repeater {
                    model: root.tags
                    Rectangle {
                        required property string modelData
                        anchors.verticalCenter: parent.verticalCenter
                        width: tagText.implicitWidth + 14
                        height: 20
                        radius: 10
                        color: "transparent"
                        border.width: 1
                        border.color: Theme.borderStrong
                        Text {
                            id: tagText
                            anchors.centerIn: parent
                            text: modelData
                            color: Theme.text
                            font.pixelSize: Theme.fontSmall
                        }
                    }
                }
                Text {
                    visible: root.stars > 0
                    anchors.verticalCenter: parent.verticalCenter
                    text: "★".repeat(root.stars)
                    color: "#f5c518"
                    font.pixelSize: Theme.fontSmall
                }
            }

            // Leftover free-form text (addon name, size, codec…), then the
            // title line when it genuinely differs from the name.
            Text {
                visible: text.length > 0 && root.structured
                text: root.detailText
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
                elide: Text.ElideRight
                maximumLineCount: 1
                width: Math.min(implicitWidth, root.width - 200)
                textFormat: Text.PlainText
            }
            Text {
                visible: text.length > 0
                text: root.subtitle
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
                elide: Text.ElideRight
                maximumLineCount: 1
                width: Math.min(implicitWidth, root.width - 200)
                textFormat: Text.PlainText
            }
        }
    }

    // Play affordance on hover.
    AppIcon {
        id: playIcon
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        glyph: Icons.play
        font.pixelSize: 22
        color: Theme.accentHover
        opacity: mouse.containsMouse ? 1 : 0
        scale: mouse.containsMouse ? 1 : 0.7
        Behavior on opacity { NumberAnimation { duration: Theme.durFast } }
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutBack } }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
