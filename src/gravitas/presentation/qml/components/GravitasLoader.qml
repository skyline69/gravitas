import QtQuick
import "."

// Orbital loading animation: a pulsing core with three dots on staggered
// orbits and expanding ripple rings — gravity, for Gravitas.
Item {
    id: root
    width: 132
    height: 132
    property bool running: visible

    // Expanding ripple rings.
    Repeater {
        model: 2
        Rectangle {
            required property int index
            anchors.centerIn: parent
            width: root.width
            height: root.height
            radius: width / 2
            color: "transparent"
            border.width: 1
            border.color: Theme.accent
            opacity: 0
            scale: 0.3

            SequentialAnimation {
                running: root.running
                loops: Animation.Infinite
                PauseAnimation { duration: index * 900 }
                ParallelAnimation {
                    NumberAnimation { property: "scale"; target: parent; from: 0.3; to: 1.0; duration: 1800; easing.type: Easing.OutCubic }
                    SequentialAnimation {
                        NumberAnimation { property: "opacity"; target: parent; from: 0; to: 0.55; duration: 300 }
                        NumberAnimation { property: "opacity"; target: parent; from: 0.55; to: 0; duration: 1500; easing.type: Easing.InQuad }
                    }
                }
            }
        }
    }

    // Three dots on staggered orbits, different radii/speeds/sizes.
    Repeater {
        model: 3
        Item {
            required property int index
            anchors.centerIn: parent
            width: 44 + index * 30
            height: width
            rotation: index * 130

            RotationAnimator on rotation {
                running: root.running
                loops: Animation.Infinite
                from: index * 130
                to: index * 130 + 360
                duration: 1100 + index * 650
            }

            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                y: -height / 2
                width: 9 - parent.index * 2
                height: width
                radius: width / 2
                color: parent.index === 0 ? Theme.text
                    : parent.index === 1 ? Theme.accentHover
                    : Theme.accent
            }
        }
    }

    // Core: soft halo + pulsing center.
    Rectangle {
        anchors.centerIn: parent
        width: 34
        height: 34
        radius: 17
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.25)
        scale: core.scale * 1.25
    }
    Rectangle {
        id: core
        anchors.centerIn: parent
        width: 16
        height: 16
        radius: 8
        color: Theme.accent
        SequentialAnimation on scale {
            running: root.running
            loops: Animation.Infinite
            NumberAnimation { from: 0.85; to: 1.2; duration: 700; easing.type: Easing.InOutQuad }
            NumberAnimation { from: 1.2; to: 0.85; duration: 700; easing.type: Easing.InOutQuad }
        }
    }
}
