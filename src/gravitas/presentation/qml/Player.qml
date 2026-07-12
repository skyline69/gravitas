import QtQuick
import QtQuick.Controls
import "components"

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
        AppButton { text: "Pause"; onClicked: playerController.pause() }
        AppButton { text: "Resume"; onClicked: playerController.resume() }
        ComboBox {
            id: subs
            textRole: "title"
            model: []
            onActivated: playerController.selectSubtitle(model[currentIndex].id)
        }
        AppButton { text: "Back"; onClicked: player.back() }
    }

    Connections {
        target: playerController
        function onSubtitleTracksChanged() { subs.model = playerController.subtitleTracks() }
    }
}
