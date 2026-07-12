import QtQuick
import Qt5Compat.GraphicalEffects
import "."

Item {
    id: root
    property string title
    property string posterUrl
    signal clicked()
    width: 160
    height: 260

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
            Rectangle {
                id: skeleton
                anchors.fill: parent
                radius: 14
                color: Theme.surface
                clip: true
                visible: img.status !== Image.Ready

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
                source: root.posterUrl ? root.posterUrl : ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
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

            MouseArea {
                id: mouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.clicked()
            }
        }

        Text {
            id: label
            width: 160
            text: root.title
            // brighten dim -> full on hover (scales with the card as one unit)
            color: mouse.containsMouse ? Theme.text : Theme.textDim
            elide: Text.ElideRight
            maximumLineCount: 2
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Behavior on color { ColorAnimation { duration: Theme.durMed } }
        }
    }
}
