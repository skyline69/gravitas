import QtQuick
import QtQuick.Controls

Item {
    id: player
    property string url
    signal back()

    onUrlChanged: if (url.length) playerController.play(url)

    // libmpv renders into the native window behind this transparent surface.
    Rectangle { anchors.fill: parent; color: "transparent" }

    Row {
        anchors.bottom: parent.bottom
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottomMargin: 24
        spacing: 12
        Button { text: "Pause"; onClicked: playerController.pause() }
        Button { text: "Resume"; onClicked: playerController.resume() }
        ComboBox {
            id: subs
            textRole: "title"
            model: playerController.subtitleTracks()
            onActivated: playerController.selectSubtitle(model[currentIndex].id)
        }
        Button { text: "Back"; onClicked: player.back() }
    }
}
