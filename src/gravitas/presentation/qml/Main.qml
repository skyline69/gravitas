pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Gravitas
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

    // ---- picture-in-picture ----
    //
    // PiP shrinks THIS window into a small frameless always-on-top tile; it
    // does not open a second one. The video item builds its render bridge
    // lazily against the window it is in, and one libmpv core allows exactly
    // one render context — moving the item between QQuickWindows is the one
    // thing this codebase must not do. Staying put means no bridge teardown
    // and identical behaviour on Linux/Vulkan, macOS/Metal and Windows/OpenGL.
    //
    // Changing flags recreates the platform window and regenerates the scene
    // graph, which the video item already survives (it detects a stale bridge,
    // the same way fullscreen makes it).
    //
    // The flags are the whole story on X11, Windows and macOS. Wayland has no
    // stay-on-top protocol, and KWin also ignores a frameless flag set on a
    // window already shown (it measured keepAbove=false, noBorder=false), so
    // WindowController asks KWin directly -- see kwin_window.py. GNOME under
    // Wayland offers no such interface; there the tile can be covered.
    property bool pipActive: false
    property real _normalX: 0
    property real _normalY: 0
    property real _normalWidth: 1280
    property real _normalHeight: 800
    property int _normalVisibility: Window.Windowed
    // Truthiness-guarded: the composition test loads this with a stub
    // controller, and a stub has no videoAspect at all.
    property real pipAspect: (PlayerController && PlayerController.videoAspect)
        ? PlayerController.videoAspect : 16 / 9

    // ---- keeping the tile the shape of the video ----
    //
    // There is deliberately NO declarative binding on `height` here. There was,
    // and it is what made resizing unstable: it wrote a new height on every
    // width change, in the middle of a resize the platform was itself driving,
    // and the platform is free to honour or drop each of those writes. The tile
    // grew vertically on some drags and not others depending on which writes
    // landed.
    //
    // Instead the geometry has exactly two sources. resizePipTo() is used by
    // the grip, which drags in app coordinates and sets both dimensions at once
    // — nothing to disagree with. Everything else (a native window edge, a
    // compositor, a video whose aspect arrives late) is reconciled once, after
    // it stops moving, by the timer below.
    function resizePipTo(width) {
        if (!window.pipActive)
            return
        var w = Math.max(240, Math.min(1280, Math.round(width)))
        window._requestSize(w, Math.round(w / window.pipAspect))
    }

    // ---- asking for a size, and keeping it ----
    //
    // Entering and leaving PiP each change things the compositor answers
    // asynchronously: a window-flag change, and on KWin the frame being taken
    // off or put back. Its answer is a configure event carrying the size the
    // window had BEFORE -- and it lands after the size this window just asked
    // for, so Qt applies the stale one on top. Measured on KWin 6.7: leaving
    // PiP restored 1280x800 and was back at 480x270 within 100ms; entering
    // with the frame removed came out 1472x828 (the old window, grown by its
    // title bar, then widened to the video's aspect). So for a moment after a
    // request the size is held: anything else the platform sets in that
    // window is answered with the size asked for.
    property int _wantWidth: 0
    property int _wantHeight: 0
    Timer { id: sizeHold; interval: 600 }
    function _requestSize(w, h) {
        window._wantWidth = w
        window._wantHeight = h
        sizeHold.restart()
        window.width = w
        window.height = h
    }
    function _holdSize() {
        if (!sizeHold.running)
            return
        if (window.width !== window._wantWidth)
            window.width = window._wantWidth
        if (window.height !== window._wantHeight)
            window.height = window._wantHeight
    }

    // A resize this window did not originate. The dimension the user moved
    // FURTHEST is treated as the one they meant, and the other is derived from
    // it — dragging a bottom edge resizes the tile vertically and pulls the
    // width along, which is what that gesture should do.
    property real _pipDragWidth: 0
    property real _pipDragHeight: 0
    // The geometry as of the PREVIOUS change. A change signal arrives after the
    // fact, so reading the size when the first one lands gives the size the
    // drag has already moved to — the origin has to be carried forward from
    // before it, or a resize that arrives in one jump measures as no movement
    // at all and the settle picks the wrong axis to derive from.
    property real _pipPrevWidth: 0
    property real _pipPrevHeight: 0
    property bool _pipSettling: false
    Timer {
        id: pipAspectSettle
        interval: 160
        onTriggered: {
            if (!window.pipActive)
                return
            window._pipSettling = true
            if (Math.abs(window.height - window._pipDragHeight)
                    > Math.abs(window.width - window._pipDragWidth))
                window.width = Math.round(window.height * window.pipAspect)
            else
                window.height = Math.round(window.width / window.pipAspect)
            window._pipSettling = false
        }
    }
    function _pipGeometryChanged() {
        // _pipSettling guards the timer's own writes: without it the correction
        // re-arms the timer that made it and the tile never stops adjusting.
        // A held size is one this window asked for, not a drag to follow.
        if (!window.pipActive || window._pipSettling || sizeHold.running) {
            window._pipPrevWidth = window.width
            window._pipPrevHeight = window.height
            return
        }
        if (!pipAspectSettle.running) {
            window._pipDragWidth = window._pipPrevWidth
            window._pipDragHeight = window._pipPrevHeight
        }
        window._pipPrevWidth = window.width
        window._pipPrevHeight = window.height
        pipAspectSettle.restart()
    }
    onWidthChanged: {
        window._holdSize()
        window._pipGeometryChanged()
    }
    onHeightChanged: {
        window._holdSize()
        window._pipGeometryChanged()
    }
    // A late aspect (mpv reports nothing until the first frame decodes) or a
    // different file: re-derive rather than leave the tile pillarboxed.
    onPipAspectChanged: {
        if (window.pipActive)
            window.height = Math.round(window.width / window.pipAspect)
    }

    function enterPip() {
        if (window.pipActive)
            return
        // Fullscreen first: a frameless always-on-top FULLSCREEN window is a
        // kiosk, and on some compositors an unescapable one.
        if (window.visibility === Window.FullScreen)
            window.visibility = Window.Windowed
        // Stashed AFTER that drop, so a maximized window comes back maximized
        // and a fullscreen one comes back windowed — deliberate: leaving PiP
        // into fullscreen would hide the window the user just re-summoned.
        window._normalVisibility = window.visibility
        window._normalX = window.x
        window._normalY = window.y
        window._normalWidth = window.width
        window._normalHeight = window.height
        // The normal minimums are sized for the Discover filter bar and would
        // refuse every tile size there is.
        window.minimumWidth = 240
        window.minimumHeight = 135
        window.flags = Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        WindowController.setFloating(true)
        window.pipActive = true
        window._pipPrevWidth = window.width
        window._pipPrevHeight = window.height
        window.resizePipTo(SettingsController ? SettingsController.pipWidth : 480)
    }

    // The compositor maximizing (or fullscreening) the tile -- KWin's maximize
    // shortcut, say -- is the viewer asking for the big window back, and it
    // arrives only as this. Unanswered, the app stayed in PiP: KWin kept the
    // tile frameless and on top (its float script still loaded), and the
    // aspect correction resized the maximized window to 16:9, taller than the
    // screen. Measured on KWin 6.7: Maximized, 1920x1036, noBorder=true.
    property bool _sizeOnUnmaximize: false
    onVisibilityChanged: {
        if (window.pipActive
                && (window.visibility === Window.Maximized
                    || window.visibility === Window.FullScreen)) {
            window.leavePip(window.visibility)
        } else if (window._sizeOnUnmaximize && window.visibility === Window.Windowed) {
            // Un-maximizing now returns to what the compositor stored before
            // its maximize: the tile. The window from before PiP is the one
            // to come back to.
            window._sizeOnUnmaximize = false
            window._requestSize(window._normalWidth, window._normalHeight)
        }
    }

    // `into` is where to leave to: the visibility stashed on entry, unless
    // the compositor has already moved the tile somewhere (see above).
    function leavePip(into) {
        if (!window.pipActive)
            return
        const target = into === undefined ? window._normalVisibility : into
        // Only the width is remembered: height follows the aspect, and a
        // Wayland client cannot place its own window, so a stored position
        // would be honoured on some platforms and ignored on others.
        if (SettingsController) {
            SettingsController.setPipWidth(Math.round(window.width))
            SettingsController.persist()
        }
        // Cleared first so the aspect Binding lets go before the minimums and
        // the old geometry go back — otherwise it fights every assignment.
        window.pipActive = false
        // Before the flags: the float script also claims windows this process
        // opens later, and a flag change can reopen this one.
        WindowController.setFloating(false)
        window.flags = Qt.Window
        window.minimumWidth = 720
        window.minimumHeight = 480
        if (into !== undefined) {
            // Already maximized by the compositor. Asking for the old size now
            // would be held against the maximize and pull the window out of
            // it; the old size is for when it is un-maximized instead.
            window._sizeOnUnmaximize = window._normalVisibility === Window.Windowed
            window.visibility = target
            return
        }
        window.x = window._normalX
        window.y = window._normalY
        window._requestSize(window._normalWidth, window._normalHeight)
        window.visibility = target
    }

    function togglePip() {
        if (window.pipActive)
            window.leavePip()
        else
            window.enterPip()
    }

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
                CatalogController.setFilter(mode)
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
                DiscoverController.open(addonId, type, catalogId)
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
        // leavePip before the pop: leaving the player while the tile is up
        // would otherwise strand a frameless, undecorated 480px window on the
        // Home page, with nothing left on screen able to restore it.
        Player {
            pipActive: window.pipActive
            onPipToggleRequested: window.togglePip()
            onPipLeaveRequested: window.leavePip()
            onPipResizeRequested: (width) => window.resizePipTo(width)
            onBack: { window.leavePip(); stack.pop() }
        }
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
        target: CatalogController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: DiscoverController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: DetailController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: PlayerController
        function onErrorOccurred(msg) { toast.show(msg, true) }
        // Not an error: playback works, the colours are wrong and the viewer
        // is the only one who can do anything about it (pick another source).
        function onPlaybackWarning(msg) { toast.show(msg, false) }
    }
    Connections {
        target: AddonController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: AddonController
        function onAddonInstalled(name) { toast.show("Installed: " + name, false) }
    }
    Connections {
        target: SettingsController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: DeepLinkController
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
        onConfirmed: DeepLinkController.confirmInstall()
        onCancelled: DeepLinkController.cancelInstall()
        onConfigureRequested: (url) => DeepLinkController.openConfigure(url)
    }
    Connections {
        target: SearchController
        function onErrorOccurred(msg) { toast.show(msg, true) }
    }
    Connections {
        target: TraktController
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
        // Null-guarded: the controllers can be gone before the engine at
        // teardown, and every live binding re-evaluates on the way down.
        active: OnboardingController !== null && OnboardingController.active
        source: "Onboarding.qml"
        onLoaded: (item as Item).forceActiveFocus()
    }
}
