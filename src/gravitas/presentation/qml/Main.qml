import QtQuick
import QtQuick.Controls
import "components"

ApplicationWindow {
    id: window
    visible: true
    width: 1280; height: 800
    title: "Gravitas"
    color: Theme.bg

    TopBar {
        id: topBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        visible: {
            var it = stack.currentItem
            return it !== null
                && (it.objectName === "homePage" || it.objectName === "settingsPage")
        }
        onTabSelected: (mode) => {
            while (stack.depth > 1) stack.pop()
            catalogController.setFilter(mode)
        }
        onOpenSettings: stack.push(settingsPage)
    }

    StackView {
        id: stack
        anchors.top: topBar.visible ? topBar.bottom : parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        initialItem: homePage
    }

    Component {
        id: homePage
        Home {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onSeeAll: (addonId, type, catalogId) => {
                discoverController.open(addonId, type, catalogId)
                stack.push(discoverPage)
            }
        }
    }
    Component {
        id: detailPage
        Detail {
            onPlayUrl: (url) => stack.push(playerPage, {url: url})
            onBack: () => stack.pop()
        }
    }
    Component {
        id: playerPage
        Player { onBack: stack.pop() }
    }
    Component {
        id: discoverPage
        Discover {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onBack: () => stack.pop()
        }
    }
    Component {
        id: settingsPage
        Settings { onBack: stack.pop() }
    }

    Connections {
        target: catalogController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: discoverController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: detailController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: playerController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: addonController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: addonController
        function onAddonInstalled(name) { toast.show("Installed: " + name, false) }
    }
    Connections {
        target: settingsController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }

    Toast { id: toast }
}
