import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt5Compat.GraphicalEffects
import "."

Rectangle {
    id: bar
    signal tabSelected(string mode)
    signal openSettings()
    signal openDetail(string type, string id)
    signal openResults()
    property string activeMode: "all"
    // True while the Settings page is showing: the gear lights up and the
    // content tabs drop their active highlight.
    property bool settingsActive: false

    height: 56
    color: Theme.surface
    radius: Theme.radius * 2
    border.width: 1
    border.color: Theme.borderStrong
    // Same soft shadow as the Discover bar: content scrolls underneath both.
    layer.enabled: true
    layer.effect: DropShadow {
        transparentBorder: true
        radius: 24
        samples: 25
        verticalOffset: 4
        color: "#66000000"
    }

    readonly property var tabs: [
        { label: "All", mode: "all", icon: Icons.dashboard, color: Theme.accent },
        { label: "Movies", mode: "movie", icon: Icons.theaters, color: "#3B82F6" },
        { label: "Series", mode: "series", icon: Icons.liveTv, color: "#22C55E" },
        { label: "Trending", mode: "trending", icon: Icons.fire, color: "#F97316" }
    ]

    // RowLayout (not anchor math + width Behavior) so the search field tracks
    // the free gap instantly during live window resizes.
    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 16
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 16

        Row {
            id: tabsRow
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
                        // No-op when re-tapping the tab already showing.
                        if (!bar.settingsActive && bar.activeMode === modelData.mode)
                            return
                        bar.activeMode = modelData.mode
                        bar.tabSelected(modelData.mode)
                    }
                }
            }
        }
    }

        // Soaks up the whole gap between the tabs and the gear; the layout
        // resizes it immediately, so live window resizes track 1:1.
        SearchBar {
            id: searchBar
            Layout.fillWidth: true
            Layout.minimumWidth: 120
            onOpenDetail: (type, id) => bar.openDetail(type, id)
            onOpenResults: bar.openResults()
        }

        AppButton {
            id: gearButton
            ghost: true
            iconGlyph: Icons.gear
            tooltip: "Settings"
            selected: bar.settingsActive
            onClicked: bar.openSettings()
        }
    }

    // Exposed so Main's click-catcher can query focus state and blur the input.
    readonly property bool searchActive: searchBar.searchActive
    function unfocusSearch() { searchBar.unfocus() }
}
