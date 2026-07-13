import QtQuick
import "."

// Section card for the Settings page: accent-bar header with title/caption,
// arbitrary content below (children land in the body column).
Rectangle {
    id: card
    property string title
    property string caption: ""
    // Entrance stagger: each card on the page passes a growing delay.
    property int enterDelay: 0
    default property alias content: body.data

    color: Theme.surface
    radius: Theme.radius * 1.5
    border.width: 1
    border.color: Theme.border
    implicitHeight: layout.implicitHeight + 40

    opacity: 0
    SequentialAnimation on opacity {
        PauseAnimation { duration: card.enterDelay }
        NumberAnimation { to: 1; duration: Theme.durMed * 2; easing.type: Easing.OutCubic }
    }

    Column {
        id: layout
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 20
        spacing: 16

        Row {
            spacing: 10
            Rectangle {
                width: 4
                height: titleCol.height
                radius: 2
                color: Theme.accent
            }
            Column {
                id: titleCol
                spacing: 2
                Text {
                    text: card.title
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    font.bold: true
                }
                Text {
                    visible: card.caption.length > 0
                    text: card.caption
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        Column {
            id: body
            width: parent.width
            spacing: 12
        }
    }
}
