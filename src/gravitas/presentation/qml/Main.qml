import QtQuick
import QtQuick.Controls
import "components"

ApplicationWindow {
    id: window
    visible: true
    width: 1280; height: 800
    // Below this the Discover filter bar's controls can't fit even at their
    // minimum widths.
    minimumWidth: 720
    minimumHeight: 480
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
        width: Math.min(parent.width - 24, 1040)
        visible: {
            var it = stack.currentItem
            return it !== null
                && (it.objectName === "homePage"
                    || it.objectName === "settingsPage"
                    || it.objectName === "searchResultsPage")
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
        onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
        onOpenResults: {
            if (stack.currentItem.objectName !== "searchResultsPage")
                stack.push(searchResultsPage)
        }
    }

    // Blurs the searchbar when the user presses anywhere in the content area
    // while it's focused. Sits above the page content (z) so its TapHandler
    // gets the press even over Flickable grids that would swallow it, but only
    // covers the region BELOW the bar (so the input itself stays interactive),
    // and is enabled only while the search input is focused. A plain Item +
    // passive TapHandler: doesn't consume clicks or affect cursor/hover.
    Item {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.top: topBar.visible ? topBar.bottom : parent.top
        z: 2
        enabled: topBar.searchActive
        TapHandler {
            gesturePolicy: TapHandler.DragThreshold
            onPressedChanged: if (pressed) topBar.unfocusSearch()
        }
    }

    StackView {
        id: stack
        // Fills the whole window; the floating bar overlays the top. Pages that
        // show the bar (Home, Settings) inset their own content below it.
        anchors.fill: parent
        initialItem: homePage

        // Fade + slight zoom instead of the default lateral slide: pushed
        // pages settle in from 2% above scale, popped pages recede the same
        // way, so navigation reads as depth rather than sideways motion.
        pushEnter: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 1.02; to: 1; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        pushExit: Transition {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.durFast }
        }
        popEnter: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        popExit: Transition {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.durFast }
            NumberAnimation { property: "scale"; from: 1; to: 1.02; duration: Theme.durFast }
        }

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
    Component {
        id: searchResultsPage
        SearchResults {
            onOpenDetail: (type, id) => stack.push(detailPage, {mediaType: type, mediaId: id})
            onBack: () => stack.pop()
        }
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
    Connections {
        target: searchController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }

    Toast { id: toast }
}
