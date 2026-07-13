import QtQuick
import "."

// Pulsing placeholder mirroring StreamRow's shape, shown while sources load.
Rectangle {
    id: root
    // Stagger offset so a stack of skeletons ripples instead of blinking in
    // unison.
    property int pulseDelay: 0

    height: 56
    radius: Theme.radiusSmall
    color: Theme.surface
    opacity: 0.55

    SequentialAnimation on opacity {
        loops: Animation.Infinite
        PauseAnimation { duration: root.pulseDelay }
        NumberAnimation { from: 0.55; to: 1.0; duration: 700; easing.type: Easing.InOutQuad }
        NumberAnimation { from: 1.0; to: 0.55; duration: 700; easing.type: Easing.InOutQuad }
    }

    Column {
        anchors.verticalCenter: parent.verticalCenter
        anchors.left: parent.left
        anchors.leftMargin: 12
        spacing: 8
        Rectangle { width: 180; height: 13; radius: 4; color: Theme.surfaceHover }
        Rectangle { width: 260; height: 10; radius: 4; color: Theme.surfaceHover }
    }
}
