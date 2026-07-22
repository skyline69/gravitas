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

    // The TopBar tab currently showing on Home. "watchlist" swaps Home's
    // catalog rows for the watchlist grid; every other mode filters the rows.
    property string catalogMode: "all"

    // Ambient drift behind every page. Hidden under the player — a movie
    // must not share the frame budget with a background nobody can see —
    // and paused with the app (inside the component).
    AmbientBackground {
        anchors.fill: parent
        visible: stack.currentItem === null
            || stack.currentItem.objectName !== "playerPage"
    }

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
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 24, 1040)
        readonly property bool shouldShow: {
            var it = stack.currentItem
            return it !== null
                && (it.objectName === "homePage"
                    || it.objectName === "settingsPage"
                    || it.objectName === "searchResultsPage")
        }
        // Fly in from above / fly out when navigating to a bar-less page,
        // fading at the same time (fade runs quicker than the slide so it
        // reads clearly). Plain `y`, not an anchor margin — Behaviors on
        // grouped anchor properties don't animate reliably. `visible` only
        // flips once fully transparent so the exit isn't cut short.
        y: shouldShow ? 12 : -(height + 24)
        opacity: shouldShow ? 1 : 0
        visible: opacity > 0
        Behavior on y {
            NumberAnimation { duration: 320; easing.type: Easing.OutCubic }
        }
        Behavior on opacity {
            NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
        }
        settingsActive: stack.currentItem !== null
            && stack.currentItem.objectName === "settingsPage"
        onTabSelected: (mode) => {
            while (stack.depth > 1) stack.pop()
            window.catalogMode = mode
            // The rows model has no "watchlist" filter — Home swaps views on
            // catalogMode instead, so the last real filter stays in place.
            if (mode !== "watchlist")
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

        // Deliberately empty, all four. StackView swallows EVERY input event
        // while a transition runs, so a fade here bought ~180ms of dead
        // clicks after each navigation: press Back, click an episode, nothing
        // happens. With nothing for StackView to run, `busy` never latches and
        // the stack is live the instant it changes; each page owns its own
        // entrance fade instead (see components/PageFade.qml).
        pushEnter: Transition {}
        pushExit: Transition {}
        popEnter: Transition {}
        popExit: Transition {}

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
            catalogMode: window.catalogMode
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
            onPlayUrl: (url, headers) => stack.push(playerPage, {url: url, headers: headers})
            onBack: () => stack.pop()
            onOpenSources: stack.push(sourcesPage)
        }
    }
    Component {
        id: sourcesPage
        Sources {
            onPlayUrl: (url, headers) => stack.push(playerPage, {url: url, headers: headers})
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
        target: deepLinkController
        function onErrorOccurred(msg) { toast.show(msg, true) }
        function onInstallRequested(info) { installDialog.ask(info) }
        // A stremio:// link was clicked in a browser: the app has to come
        // forward, or the dialog opens behind whatever the user is looking at.
        function onActivateRequested() {
            window.show()
            window.raise()
            window.requestActivate()
        }
    }

    AddonInstallDialog {
        id: installDialog
        onConfirmed: deepLinkController.confirmInstall()
        onCancelled: deepLinkController.cancelInstall()
        onConfigureRequested: (url) => deepLinkController.openConfigure(url)
    }
    Connections {
        target: searchController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: traktController
        function onErrorOccurred(msg) { toast.show(msg, true) }
        function onSyncCompleted(applied) {
            toast.show(applied === 0
                ? "Trakt: nothing new to sync"
                : "Trakt: synced " + applied + (applied === 1 ? " title" : " titles"), false)
        }
    }

    Toast { id: toast }

    // First-run wizard, above everything including the toast. Active only on
    // a fresh install; the wizard runs its exit fade BEFORE complete() flips
    // active, so the Loader only unloads once the overlay is invisible.
    Loader {
        anchors.fill: parent
        z: 100
        // Null-guarded: context properties go null at engine teardown and
        // every live binding re-evaluates on the way down.
        active: onboardingController !== null && onboardingController.active
        source: "Onboarding.qml"
        onLoaded: item.forceActiveFocus()
    }
}
