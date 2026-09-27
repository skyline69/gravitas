import QtQuick

// Themed checkbox: box + label, whole row clickable. Stateless on purpose —
// `checked` is a plain binding to the backend and `toggled` reports the
// requested value; the controller's notify signal flips the visual, so the
// box can never drift from persisted state.
Item {
    id: root
    property bool checked: false
    property string label: ""
    property string tooltip: ""
    signal toggled(bool checked)

    implicitWidth: row.implicitWidth
    implicitHeight: 24

    Row {
        id: row
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8

        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 18; height: 18
            radius: 4
            color: root.checked ? Theme.accent : "transparent"
            border.width: root.checked ? 0 : 1
            border.color: mouse.containsMouse ? Theme.textDim : Theme.borderStrong
            Behavior on color { ColorAnimation { duration: Theme.durFast } }

            AppIcon {
                anchors.centerIn: parent
                glyph: Icons.check
                font.pixelSize: 14
                color: "white"
                visible: root.checked
            }
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.label
            color: mouse.containsMouse ? Theme.text : Theme.textDim
            font.pixelSize: Theme.fontSmall
            Behavior on color { ColorAnimation { duration: Theme.durFast } }
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: row
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.toggled(!root.checked)
        onContainsMouseChanged: {
            if (root.tooltip.length === 0)
                return
            if (containsMouse)
                tipTimer.restart()
            else {
                tipTimer.stop()
                tip.close()
            }
        }
    }

    Timer { id: tipTimer; interval: 400; onTriggered: tip.open() }
    AppToolTip {
        id: tip
        text: root.tooltip
        x: 0
        y: root.height + 6
    }
}
