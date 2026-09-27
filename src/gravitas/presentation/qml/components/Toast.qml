import QtQuick
import "."

// Floating, auto-dismissing notification. Call show(message, isError).
Item {
    id: root
    anchors.fill: parent
    z: 200
    // let clicks pass through everywhere except the card itself
    property int autoHideMs: 4500

    function show(message, isError) {
        label.text = message
        stripe.color = isError ? Theme.danger : Theme.accent
        root.state = "shown"
        hideTimer.restart()
    }

    Timer {
        id: hideTimer
        interval: root.autoHideMs
        onTriggered: root.state = ""
    }

    Rectangle {
        id: card
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.rightMargin: 20
        anchors.topMargin: 16
        // shrink to content: stripe + left pad + row + right pad, capped so a
        // long message wraps instead of stretching across the window
        width: stripe.width + 14 + row.width + 14
        implicitHeight: Math.max(48, row.implicitHeight + 24)
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: Theme.border
        opacity: 0
        visible: opacity > 0
        // slide down into place from just above
        transform: Translate { id: slide; y: -20 }

        // colour accent stripe (danger for errors, accent for info)
        Rectangle {
            id: stripe
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 4
            topLeftRadius: card.radius
            bottomLeftRadius: card.radius
            color: Theme.accent
        }

        Row {
            id: row
            anchors.left: stripe.right
            anchors.leftMargin: 14
            anchors.verticalCenter: parent.verticalCenter
            spacing: 12

            Text {
                id: label
                anchors.verticalCenter: parent.verticalCenter
                // natural width until it hits the cap, then wrap
                width: Math.min(implicitWidth, 360)
                color: Theme.text
                font.pixelSize: Theme.fontSmall
                wrapMode: Text.WordWrap
                // An addon that refuses a request answers with a sentence of
                // its own (see _addon_error_detail); three lines cut it off.
                maximumLineCount: 4
                elide: Text.ElideRight
            }

            AppIcon {
                id: closeBtn
                anchors.verticalCenter: parent.verticalCenter
                glyph: Icons.x
                font.pixelSize: Theme.fontBody
                color: closeMouse.containsMouse ? Theme.text : Theme.textDim
                Behavior on color { ColorAnimation { duration: Theme.durFast } }
                MouseArea {
                    id: closeMouse
                    anchors.fill: parent
                    anchors.margins: -6
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.state = ""
                }
            }
        }
    }

    states: State {
        name: "shown"
        PropertyChanges { card.opacity: 1 }
        PropertyChanges { slide.y: 0 }
    }

    transitions: Transition {
        NumberAnimation {
            target: card; property: "opacity"
            duration: Theme.durMed; easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: slide; property: "y"
            duration: Theme.durMed; easing.type: Easing.OutCubic
        }
    }
}
