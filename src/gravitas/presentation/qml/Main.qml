import QtQuick
import QtQuick.Controls
import "components"

ApplicationWindow {
    id: window
    visible: true
    width: 1280; height: 800
    title: "Gravitas"
    color: Theme.bg

    // Neutral focus sink OUTSIDE the StackView's focus scope. Moving active
    // focus here truly blurs a focused field; forcing focus onto the StackView
    // (itself a FocusScope) would just re-delegate focus back to the field.
    Item { id: focusSink }

    TopBar {
        id: topBar
        // Floating bar: a shorter, horizontally-centered pill that sits ON TOP
        // of the content (z:1) with a margin from the top edge. Content scrolls
        // underneath it rather than being cut off in a gap.
        z: 1
        anchors.top: parent.top
        anchors.topMargin: 12
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 24, 820)
        visible: {
            var it = stack.currentItem
            return it !== null
                && (it.objectName === "homePage" || it.objectName === "settingsPage")
        }
        settingsActive: stack.currentItem !== null
            && stack.currentItem.objectName === "settingsPage"
        onTabSelected: (mode) => {
            while (stack.depth > 1) stack.pop()
            catalogController.setFilter(mode)
        }
        onOpenSettings: {
            if (stack.currentItem.objectName !== "settingsPage")
                stack.push(settingsPage)
        }
    }

    StackView {
        id: stack
        // Fills the whole window; the floating bar overlays the top. Pages that
        // show the bar (Home, Settings) inset their own content below it.
        anchors.fill: parent
        initialItem: homePage

        // Tapping empty space clears keyboard focus (e.g. blurs a focused
        // text field). Controls consume their own taps, so this only fires
        // for otherwise-unhandled taps on empty areas. Focus goes to the
        // window-level sink (not the StackView, which would bounce it back).
        TapHandler {
            onTapped: focusSink.forceActiveFocus()
        }
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
