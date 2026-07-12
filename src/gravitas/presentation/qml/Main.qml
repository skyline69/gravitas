import QtQuick
import QtQuick.Controls

ApplicationWindow {
    id: window
    visible: true
    width: 1280; height: 800
    title: "Gravitas"
    color: "#141414"

    StackView {
        id: stack
        anchors.fill: parent
        initialItem: homePage
    }

    Component {
        id: homePage
        Home { onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id}) }
    }
    Component {
        id: detailPage
        Detail { onPlayUrl: (url) => stack.push(playerPage, {url: url}) }
    }
    Component {
        id: playerPage
        Player { onBack: stack.pop() }
    }

    Connections {
        target: catalogController
        function onErrorOccurred(msg) { errorBar.show(msg) }
    }
    Connections {
        target: detailController
        function onErrorOccurred(msg) { errorBar.show(msg) }
    }
    Connections {
        target: playerController
        function onErrorOccurred(msg) { errorBar.show(msg) }
    }

    Rectangle {
        id: errorBar
        function show(msg) { label.text = msg; visible = true; hideTimer.restart() }
        visible: false
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 40; color: "#902020"; z: 100
        Text { id: label; anchors.centerIn: parent; color: "white" }
        Timer { id: hideTimer; interval: 4000; onTriggered: errorBar.visible = false }
    }
}
