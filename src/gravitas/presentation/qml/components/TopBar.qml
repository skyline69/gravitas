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
        { label: "All", mode: "all", icon: Icons.dashboard },
        { label: "Movies", mode: "movie", icon: Icons.theaters },
        { label: "Series", mode: "series", icon: Icons.liveTv },
        { label: "Trending", mode: "trending", icon: Icons.fire }
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
                width: 44
                height: 36

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radius
                    color: bar.activeMode === modelData.mode
                        ? Theme.surfacePress
                        : (tabHover.hovered ? Theme.surfaceHover : "transparent")
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                }
                AppIcon {
                    anchors.centerIn: parent
                    glyph: modelData.icon
                    font.pixelSize: Theme.fontTitle
                    color: bar.activeMode === modelData.mode ? Theme.accent : Theme.text
                }
                HoverHandler { id: tabHover; cursorShape: Qt.PointingHandCursor }
                ToolTip.text: modelData.label
                ToolTip.visible: tabHover.hovered
                ToolTip.delay: 400
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
