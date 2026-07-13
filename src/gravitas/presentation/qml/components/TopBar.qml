import QtQuick
import QtQuick.Controls
import "."

Rectangle {
    id: bar
    signal tabSelected(string mode)
    signal openSettings()
    property string activeMode: "all"
    // True while the Settings page is showing: the gear lights up and the
    // content tabs drop their active highlight.
    property bool settingsActive: false

    height: 56
    color: Theme.surface
    radius: Theme.radius * 2
    border.width: 1
    border.color: Theme.borderStrong

    readonly property var tabs: [
        { label: "All", mode: "all", icon: Icons.dashboard, color: Theme.accent },
        { label: "Movies", mode: "movie", icon: Icons.theaters, color: "#3B82F6" },
        { label: "Series", mode: "series", icon: Icons.liveTv, color: "#22C55E" },
        { label: "Trending", mode: "trending", icon: Icons.fire, color: "#F97316" }
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
                readonly property bool active: !bar.settingsActive && bar.activeMode === modelData.mode
                readonly property color activeColor: modelData.color
                width: 44
                height: 36

                Rectangle {
                    anchors.fill: parent
                    radius: Theme.radius
                    // Active tab gets a faint wash of its own accent colour;
                    // hover is the neutral surface highlight.
                    color: active
                        ? Qt.rgba(activeColor.r, activeColor.g, activeColor.b, 0.16)
                        : (tabHover.hovered ? Theme.surfaceHover : "transparent")
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                }
                AppIcon {
                    anchors.centerIn: parent
                    glyph: modelData.icon
                    font.pixelSize: Theme.fontTitle
                    color: active ? activeColor : Theme.text
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                    // Springy pop when the tab becomes active; settles back when
                    // it goes inactive.
                    scale: active ? 1.18 : 1.0
                    Behavior on scale {
                        NumberAnimation {
                            duration: Theme.durMed
                            easing.type: Easing.OutBack
                            easing.overshoot: 3.5
                        }
                    }
                }
                HoverHandler {
                    id: tabHover
                    cursorShape: Qt.PointingHandCursor
                    onHoveredChanged: {
                        if (tabHover.hovered)
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
                    text: modelData.label
                    x: (parent.width - width) / 2
                    y: parent.height + 8
                }
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
        tooltip: "Settings"
        selected: bar.settingsActive
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        onClicked: bar.openSettings()
    }
}
