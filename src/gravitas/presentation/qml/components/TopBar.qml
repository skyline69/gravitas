import QtQuick
import QtQuick.Controls
import "."

Rectangle {
    id: bar
    signal tabSelected(string mode)
    signal openSettings()
    property string activeMode: "all"

    height: 56
    color: Theme.surface

    readonly property var tabs: [
        { label: "All", mode: "all" },
        { label: "Movies", mode: "movie" },
        { label: "Series", mode: "series" },
        { label: "Trending", mode: "trending" }
    ]

    Row {
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8

        Repeater {
            model: bar.tabs
            delegate: Item {
                required property var modelData
                width: tabLabel.implicitWidth + 24
                height: 36

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radius
                    color: bar.activeMode === modelData.mode
                        ? Theme.surfacePress
                        : (tabHover.hovered ? Theme.surfaceHover : "transparent")
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                }
                Text {
                    id: tabLabel
                    anchors.centerIn: parent
                    text: modelData.label
                    font.pixelSize: Theme.fontBody
                    color: bar.activeMode === modelData.mode ? Theme.accent : Theme.text
                }
                HoverHandler { id: tabHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        bar.activeMode = modelData.mode
                        bar.tabSelected(modelData.mode)
                    }
                }
            }
        }
    }

    AppButton {
        id: gearButton
        ghost: true
        iconGlyph: Icons.gear
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        onClicked: bar.openSettings()
    }
}
