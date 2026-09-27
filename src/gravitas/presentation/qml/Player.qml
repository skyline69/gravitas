pragma ComponentBehavior: Bound
import QtQuick
// StackView's attached properties (hotkey gating) live in Controls.
import QtQuick.Controls
import Gravitas 1.0
import "components"

Item {
    id: player
    // Main hides the ambient background under this page (see Main.qml).
    objectName: "playerPage"
    property string url
    // behaviorHints.proxyHeaders.request of the chosen stream.
    property var headers: ({})
    signal back()

    // Fades itself in; StackView's transitions are empty so the stack stays
    // responsive during the animation (see PageFade). The exit fade is this
    // page's own blackout rectangle, below.
    opacity: 0
    PageFade { id: pageFade; target: player }
    StackView.onActivating: pageFade.restart()

    // Deliberately NOT onUrlChanged: StackView applies initial properties one
    // at a time between beginCreate() and completeCreate(), so `url` arriving
    // first would start playback while `headers` was still its default {} --
    // silently dropping the Referer a proxyHeaders stream needs. By
    // onCompleted every initial property is in place.

    // Track selection state for the check mark in the menus. -1 = default/off.
    property int currentAudio: -1
    property int currentSub: -1
    property bool controlsVisible: true

    // Polled (not bound): the duration property's change signal comes off
    // mpv's event thread; polling alongside position guarantees the timeline
    // range is right even if that delivery hiccups — a stale `to` of 1 made
    // mid-timeline drags seek to the file's first second.
    property real dur: 0
    // Polled buffering state; loader shows for the initial open too (no
    // duration yet). A short on-delay keeps fast seeks from flashing it.
    property bool buffering: false
    // Absolute position (seconds) the demuxer has downloaded up to; drives
    // the lighter loaded-ahead track in the timeline.
    property real bufferedTo: 0
    readonly property bool mediaLoading: player.url.length > 0 && (dur <= 0 || buffering)
    property bool showLoader: false
    onMediaLoadingChanged: {
        if (mediaLoading) {
            loaderDelay.restart()
        } else {
            loaderDelay.stop()
            showLoader = false
        }
    }
    Timer { id: loaderDelay; interval: 250; onTriggered: player.showLoader = true }
    readonly property bool isFullscreen: Window.window
        && Window.window.visibility === Window.FullScreen
    // The window shrunk to a picture-in-picture tile. PiP belongs to the
    // window (Main.qml), so the page is TOLD whether it is on and ASKS for
    // changes through the signals below -- it used to call functions on
    // Window.window, which is typed as a plain Window and so could only be
    // reached untyped (and was absent entirely in the composition test).
    property bool pipActive: false
    readonly property bool pip: player.pipActive
    signal pipToggleRequested()
    signal pipLeaveRequested()
    signal pipResizeRequested(real width)
    // The keyboard OSD is sized for a full window; at tile size it covers the
    // film it is meant to annotate. Scale it with the tile, floored so the
    // glyph stays readable at the 240px minimum rather than shrinking to a
    // speck, and pinned at 1 outside PiP so nothing changes there.
    readonly property real osdScale: player.pip
        ? Math.max(0.5, Math.min(1, player.width / 960))
        : 1

    // ---- pause while the window is being resized ----
    // A drag arrives as a stream of geometry changes; entering PiP or going
    // fullscreen arrives as one or two. Counting them is what separates the
    // two, and only the first is worth pausing for — pausing on a PiP toggle
    // would put a hole in the audio every time the tile is summoned.
    //
    // Deliberately driven off the page's own size rather than the window's:
    // this page fills the window, and reading it here needs no null guard for
    // the composition test's stub window.
    property int _resizeTicks: 0
    readonly property int _resizeDragTicks: 3
    function _noteResize() {
        if (!PlayerController)
            return
        player._resizeTicks += 1
        if (player._resizeTicks >= player._resizeDragTicks)
            PlayerController.suspendForResize()
        resizeSettle.restart()
    }
    onWidthChanged: player._noteResize()
    onHeightChanged: player._noteResize()
    Timer {
        id: resizeSettle
        interval: 180
        onTriggered: {
            player._resizeTicks = 0
            if (PlayerController)
                PlayerController.resumeAfterResize()
        }
    }

    function showControls() {
        controlsVisible = true
        hideTimer.restart()
    }
    function toggleFullscreen() {
        var w = Window.window
        if (!w)
            return
        w.visibility = player.isFullscreen ? Window.Windowed : Window.FullScreen
    }
    // PiP is the window's business, not the page's — these only forward.
    // They live on the root item because hotkeys (Shortcuts) route through it.
    function togglePip() {
        player.pipToggleRequested()
    }
    function leavePip() {
        player.pipLeaveRequested()
    }
    // Smooth exit: fade the page and the audio together, THEN stop and pop —
    // a bare stop() cuts sound and freezes the frame mid-scene.
    property real _restoreVol: 100
    function leave() {
        if (exitAnim.running)
            return
        if (player.isFullscreen)
            Window.window.visibility = Window.Windowed
        player._restoreVol = PlayerController.volume
        volFader.v = PlayerController.volume
        exitAnim.start()
    }
    QtObject {
        id: volFader
        property real v: 100
        onVChanged: PlayerController.setVolume(v)
    }
    // Fade to BLACK, not to transparency: dimming the page's own opacity
    // blends the video toward the window background (reads as gray) because
    // the underlying stack page isn't visible during the fade.
    Rectangle {
        id: blackout
        z: 100
        anchors.fill: parent
        color: "black"
        opacity: 0
        visible: opacity > 0
    }
    SequentialAnimation {
        id: exitAnim
        ParallelAnimation {
            NumberAnimation {
                target: blackout
                property: "opacity"
                from: 0; to: 1
                duration: 260
                easing.type: Easing.OutCubic
            }
            NumberAnimation { target: volFader; property: "v"; to: 0; duration: 240 }
        }
        // Pop immediately when the fade lands; stopping mpv here would stall
        // the GUI thread for a beat and make the page switch feel like a cut.
        // The audio is already faded to zero, so playback keeps "running"
        // inaudibly through the pop transition and stops at teardown.
        ScriptAction { script: player.back() }
    }
    Component.onDestruction: {
        // Belt and braces with Main's onBack: whatever tears this page down,
        // the window must not be left frameless and 480px wide with no player
        // in it to restore itself.
        player.leavePip()
        PlayerController.stop()
        // Restore the user's volume silently for the next playback.
        PlayerController.setVolume(player._restoreVol)
    }
    function fmt(s) {
        s = Math.max(0, Math.floor(s))
        var h = Math.floor(s / 3600)
        var m = Math.floor((s % 3600) / 60)
        var sec = s % 60
        function pad(n) { return (n < 10 ? "0" : "") + n }
        return h > 0 ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec)
    }

    // Self-contained pill: the shared Toast lives in Main.qml and the player
    // page has no handle on it.
    Rectangle {
        id: resumePill
        z: 50
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 24
        width: resumeLabel.implicitWidth + 28
        height: 40
        radius: 20
        color: Qt.rgba(0, 0, 0, 0.78)
        border.width: 1
        border.color: Theme.borderStrong
        opacity: 0
        visible: opacity > 0 && !player.pip
        Text {
            id: resumeLabel
            anchors.centerIn: parent
            color: Theme.text
            font.pixelSize: Theme.fontSmall
        }
        SequentialAnimation {
            id: resumeFade
            NumberAnimation { target: resumePill; property: "opacity"; to: 1; duration: Theme.durMed }
            PauseAnimation { duration: 2600 }
            NumberAnimation { target: resumePill; property: "opacity"; to: 0; duration: Theme.durMed }
        }
    }

    Connections {
        target: PlayerController
        function onResumed(position) {
            resumeLabel.text = "Resumed from " + player.fmt(position)
            resumeFade.restart()
        }
    }

    Component.onCompleted: {
        pageFade.restart()
        if (player.url.length)
            PlayerController.play(player.url, player.headers)
    }

    // ---- hotkeys ----
    // Window-scoped Shortcuts, NOT Keys.onPressed: a Keys handler only fires
    // while its Item holds active focus, and anything that shuffles focus (a
    // track menu closing, a fullscreen switch, plain window-manager churn
    // during a long hands-off stretch) silently killed every hotkey until the
    // user clicked the video again. Shortcuts fire regardless of focus, so
    // they are gated instead: only while this page is the stack's current
    // item and no track menu is open (menus keep their own arrow/Esc keys).
    readonly property bool hotkeysOn: player.StackView.status === StackView.Active
        && !subsMenu.visible && !audioMenu.visible && !episodePanel.searchFocused

    // ---- episode panel ----
    // A series episode, with the series still loaded underneath: the only way
    // here. Truthiness rather than bare reads -- the composition test loads
    // this page with stub controllers.
    readonly property bool hasEpisodes: !!(PlayerController && PlayerController.videoId)
        && !!(DetailController && DetailController.hasEpisodes)
    property bool episodesOpen: false
    function toggleEpisodes() {
        if (!player.hasEpisodes || player.pip)
            return
        player.episodesOpen = !player.episodesOpen
        player.showControls()
    }
    // ---- sections the file's chapters name ----
    // Intro, recap and end credits, read off the chapters by the controller
    // (application/segments.py): -1 where the file says nothing, and nothing
    // here guesses in their place.
    readonly property real creditsAt: PlayerController && PlayerController.creditsStart > 0
        ? PlayerController.creditsStart : -1
    // "intro" or "recap" while playback is inside one (up to a second before
    // its end, so the button does not flash on the way out), else "".
    // Only once playback is really under way: while a resumed episode is still
    // opening (or seeking to where it left off) the playhead reads 0:00, which
    // is inside an intro that starts the episode, and the button flashed up
    // for a moment before the resume landed at 3:21.
    readonly property string skipKind: {
        if (!PlayerController || player.pip || player.dur <= 0 || player.buffering)
            return ""
        const at = timeline.value
        // A playhead still at zero has not started anywhere yet.
        if (at <= 0.25)
            return ""
        if (PlayerController.recapEnd > 0 && at >= PlayerController.recapStart
                && at < PlayerController.recapEnd - 1)
            return "recap"
        if (PlayerController.introEnd > 0 && at >= PlayerController.introStart
                && at < PlayerController.introEnd - 1)
            return "intro"
        return ""
    }
    // " · Intro" for the section `at` falls in, "" for an unnamed one.
    function sectionTitleAt(at: real): string {
        const list = PlayerController ? PlayerController.timelineSections : []
        if (!list)
            return ""
        for (let i = 0; i < list.length; i++) {
            if (at >= list[i].start && at < list[i].end)
                return list[i].title ? " · " + list[i].title : ""
        }
        return ""
    }
    function skipSection() {
        if (player.skipKind === "")
            return
        PlayerController.seek(player.skipKind === "recap"
            ? PlayerController.recapEnd : PlayerController.introEnd)
        player.showControls()
    }

    // ---- next episode ----
    // Offered once the episode's story is over: from the start of its end
    // credits when the chapters say where that is, else over its last
    // `nextWindowS` seconds; and on the held last frame after it. Seeking back
    // takes it away again. The episode after this one in watching order -- across a
    // season boundary, never from a regular episode into the specials, and
    // nothing after a finale (DetailController.nextEpisode).
    readonly property int nextWindowS: 40
    readonly property var nextUp: player.hasEpisodes
        ? DetailController.nextEpisode(PlayerController.videoId) : ({})
    readonly property bool showNext: !player.pip && !!player.nextUp.videoId && player.dur > 0
        && (player.creditsAt > 0 ? timeline.value >= player.creditsAt
                                 : player.dur - timeline.value <= player.nextWindowS)
    // Whether the panel's last message answers a click on the card.
    property bool _nextRequested: false
    function playNext() {
        if (!player.nextUp.videoId)
            return
        player._nextRequested = true
        // The panel's own path: sources fetched as the Sources page would,
        // the best one played, the rest queued behind it.
        episodePanel.pick(player.nextUp.videoId, player.nextUp.season,
                          player.nextUp.episode, player.nextUp.title)
    }
    Connections {
        target: PlayerController
        function onMediaContextChanged() { player._nextRequested = false }
    }

    // Leaving PiP or losing the series closes it: neither has room for it.
    onPipChanged: if (player.pip) player.episodesOpen = false
    onHasEpisodesChanged: if (!player.hasEpisodes) player.episodesOpen = false

    function kbTogglePause() {
        player.showControls()
        PlayerController.togglePause()
        // Flash the NEW state, YouTube-style: pausing shows the pause bars.
        osd.flash(PlayerController.paused ? Icons.pause : Icons.play)
    }
    function kbSeek(delta) {
        player.showControls()
        PlayerController.seekBy(delta)
        if (delta <= -60) osd.flash(Icons.fastRewind, "60s")
        else if (delta < 0) osd.flash(Icons.replay10)
        else if (delta >= 60) osd.flash(Icons.fastForward, "60s")
        else osd.flash(Icons.forward10)
    }
    function kbVolume(delta) {
        player.showControls()
        PlayerController.setVolume(
            Math.max(0, Math.min(100, PlayerController.volume + delta)))
        osd.flash(PlayerController.volume > 0 ? Icons.volumeUp : Icons.volumeOff,
                  Math.round(PlayerController.volume) + "%")
    }

    Shortcut {
        enabled: player.hotkeysOn
        sequences: ["Space", "K"]
        autoRepeat: false // a held Space must not machine-gun pause toggles
        onActivated: player.kbTogglePause()
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequences: ["Left", "J"]
        onActivated: player.kbSeek(-10)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequences: ["Right", "L"]
        onActivated: player.kbSeek(10)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "Shift+Left"
        onActivated: player.kbSeek(-60)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "Shift+Right"
        onActivated: player.kbSeek(60)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "Up"
        onActivated: player.kbVolume(5)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "Down"
        onActivated: player.kbVolume(-5)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "F"
        autoRepeat: false
        onActivated: { player.showControls(); player.toggleFullscreen() }
    }
    Shortcut {
        enabled: player.hotkeysOn && player.skipKind !== ""
        sequence: "S"
        autoRepeat: false
        onActivated: player.skipSection()
    }
    Shortcut {
        enabled: player.hotkeysOn && player.showNext
        sequence: "N"
        autoRepeat: false
        onActivated: player.playNext()
    }
    Shortcut {
        enabled: player.hotkeysOn && player.hasEpisodes
        sequence: "E"
        autoRepeat: false
        onActivated: player.toggleEpisodes()
    }
    // Subtitle timing, VLC's keys: G earlier, H later, 0.1s a press. For a
    // subtitle made for another release of the episode.
    function kbShiftSubtitles(delta) {
        const offset = PlayerController.shiftSubtitles(delta)
        const text = offset === 0 ? "In sync"
            : (offset > 0 ? "+" : "") + offset.toFixed(1) + "s"
        osd.flash(Icons.subtitles, text)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "G"
        onActivated: player.kbShiftSubtitles(-0.1)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "H"
        onActivated: player.kbShiftSubtitles(0.1)
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "P"
        autoRepeat: false
        onActivated: player.togglePip()
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "M"
        autoRepeat: false
        onActivated: {
            player.showControls()
            PlayerController.toggleMute()
            osd.flash(PlayerController.muted ? Icons.volumeOff : Icons.volumeUp,
                      PlayerController.muted ? "Muted" : Math.round(PlayerController.volume) + "%")
        }
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "Escape"
        autoRepeat: false
        // Window.window is an Item-attached property; on a Shortcut it is
        // null, so going through the root item's function is the only way.
        onActivated: {
            if (player.episodesOpen)
                player.episodesOpen = false
            else if (player.pip)
                player.leavePip()
            else if (player.isFullscreen)
                player.toggleFullscreen()
            else
                player.leave()
        }
    }
    // 0-9: jump to that tenth of the runtime (5 -> 50%), like mpv/YT.
    // No-op before the duration is known. Shortcut is not an Item, so the
    // ten of them come from an Instantiator, not a Repeater.
    Instantiator {
        model: 10
        delegate: Shortcut {
            required property int index
            enabled: player.hotkeysOn
            sequence: String(index)
            autoRepeat: false
            onActivated: {
                if (player.dur > 0) {
                    player.showControls()
                    PlayerController.seek(player.dur * index / 10)
                }
            }
        }
    }

    Rectangle { anchors.fill: parent; color: "black" }

    // Registered at runtime in main.py (the concrete class is per platform);
    // described for tooling in Gravitas/gravitas.qmltypes.
    MpvVideo {
        id: video
        anchors.fill: parent
        Component.onCompleted: PlayerController.attachVideo(video)
    }

    // Idle auto-hide: any pointer motion revives the controls; they fade out
    // 2.5s later unless a menu is open or the timeline is being dragged.
    Timer {
        id: hideTimer
        interval: 2500
        onTriggered: {
            if (!subsMenu.visible && !audioMenu.visible && !timeline.pressed)
                player.controlsVisible = false
        }
    }
    Timer {
        id: posTimer
        interval: 500
        running: true
        repeat: true
        onTriggered: {
            player.dur = PlayerController.duration
            player.buffering = PlayerController.isLoading()
            player.bufferedTo = PlayerController.bufferedTo()
            if (!timeline.pressed)
                timeline.value = PlayerController.position()
        }
    }

    // Single click pauses only after the double-click window passes —
    // otherwise a double-click (fullscreen) also toggles pause twice.
    Timer {
        id: singleClickTimer
        interval: 220
        onTriggered: { PlayerController.togglePause(); player.showControls() }
    }
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        // Never blank the cursor on a tile: the pointer is how the window is
        // dragged and resized, so it has to stay visible.
        cursorShape: (player.pip || player.controlsVisible)
            ? Qt.ArrowCursor : Qt.BlankCursor
        onPositionChanged: player.showControls()
        // In PiP the press IS the window drag — the compositor takes the
        // pointer grab, which is the only way a frameless window moves under
        // Wayland. Click-to-pause goes with it; pausing is the hover bar's
        // button and Space.
        onPressed: {
            if (player.pip && Window.window)
                Window.window.startSystemMove()
        }
        onClicked: {
            // With the panel open, a click on the film puts it away rather
            // than pausing what the viewer is looking past it at.
            if (player.episodesOpen)
                player.episodesOpen = false
            else if (!player.pip)
                singleClickTimer.restart()
        }
        onDoubleClicked: {
            singleClickTimer.stop()
            if (player.pip)
                player.leavePip()
            else
                player.toggleFullscreen()
        }
    }

    // ---- keyboard OSD flash (YouTube-style) ----
    // A centered circle that pops and fades when a KEYBOARD action changes
    // playback state. Pointer-driven changes stay silent: the buttons the
    // user just clicked are feedback enough, and the flash would sit right
    // where the film is.
    Item {
        id: osd
        z: 9
        anchors.centerIn: parent
        width: Math.round(84 * player.osdScale)
        height: width
        opacity: 0
        visible: opacity > 0

        property string glyph: ""
        property string label: ""
        readonly property bool hasLabel: label.length > 0
        function flash(glyph, label) {
            osd.glyph = glyph
            osd.label = label === undefined ? "" : label
            osdAnim.restart()
        }

        Rectangle {
            anchors.fill: parent
            radius: Math.round(18 * player.osdScale)
            color: Qt.rgba(0, 0, 0, 0.55)
        }
        AppIcon {
            id: osdIcon
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            // The Text box includes the font's descent below the glyph (icon
            // glyphs sit on the baseline), so a plain centerIn rides the glyph
            // high — push it down by half the descent to center it optically.
            // With a label, lift the pair so icon + label center together.
            anchors.verticalCenterOffset: osdIconMetrics.descent / 2
                - (osd.hasLabel ? 9 * player.osdScale : 0)
            glyph: osd.glyph
            font.pixelSize: Math.round((osd.hasLabel ? 32 : 40) * player.osdScale)
            color: "white"
        }
        FontMetrics { id: osdIconMetrics; font: osdIcon.font }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Math.round(12 * player.osdScale)
            visible: osd.hasLabel
            text: osd.label
            color: "white"
            font.pixelSize: Math.round(Theme.fontSmall * player.osdScale)
            font.bold: true
        }
        // Pop in fast at full strength, then grow gently while fading out —
        // reads as an acknowledgement, not a dialog.
        ParallelAnimation {
            id: osdAnim
            NumberAnimation {
                target: osd
                property: "scale"
                from: 0.8; to: 1.25
                duration: 700
                easing.type: Easing.OutCubic
            }
            SequentialAnimation {
                NumberAnimation { target: osd; property: "opacity"; from: 0; to: 1; duration: 90 }
                PauseAnimation { duration: 160 }
                NumberAnimation {
                    target: osd
                    property: "opacity"
                    to: 0
                    duration: 450
                    easing.type: Easing.InQuad
                }
            }
        }
    }

    // ---- loading overlay ----
    Rectangle {
        id: loadingOverlay
        z: 5
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.45)
        opacity: player.showLoader ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        Column {
            anchors.centerIn: parent
            spacing: 18
            GravitasLoader {
                anchors.horizontalCenter: parent.horizontalCenter
                running: loadingOverlay.visible
            }
            // Says why the spinner is there: a dropped connection is being
            // waited out, not a slow open. Truthiness, not a bare read: the
            // composition test loads this page with a stub controller.
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: PlayerController && PlayerController.reconnecting
                text: qsTr("Reconnecting…")
                color: "white"
                font.pixelSize: Theme.fontSmall
            }
        }
    }

    // ---- top title overlay ----
    // Fades with the controls, like the bottom bar. Hidden entirely when the
    // context carries no name (trailers play without an identity).
    Rectangle {
        z: 10
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: 108
        // Truthiness, not .length: the composition test loads this page with
        // a stub controller where mediaTitle is undefined.
        opacity: player.controlsVisible && !player.pip
            && PlayerController && PlayerController.mediaTitle ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.88) }
            GradientStop { position: 0.45; color: Qt.rgba(0, 0, 0, 0.72) }
            GradientStop { position: 1.0; color: "transparent" }
        }

        Column {
            id: titleColumn
            // Clear of the back button on both sides so a long title stays
            // centered instead of sliding under it.
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 20
            width: parent.width - 176
            spacing: 2

            Text {
                id: titleText
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                text: (PlayerController && PlayerController.mediaTitle) || ""
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.bold: true
            }
            Text {
                id: labelText
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                visible: !!(PlayerController && PlayerController.mediaLabel)
                text: (PlayerController && PlayerController.mediaLabel) || ""
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
        }
    }

    // ---- top-left back button ----
    BackButton {
        id: backButton
        z: 10
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 16
        blurTarget: video
        opacity: player.controlsVisible && !player.pip ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        onClicked: player.leave()
    }

    EpisodePanel {
        id: episodePanel
        z: 12
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        width: Math.min(440, Math.max(340, player.width * 0.3))
        open: player.episodesOpen
        onCloseRequested: player.episodesOpen = false
        onPlayFirstSource: player.playFromList()
    }

    // The same thing a click on the Sources page's first row does, from the
    // list DetailController just filled for the chosen episode: its identity,
    // its label, the rest of the list to fall back to, then play. Stopped
    // first, so the episode being left records its position and scrobbles a
    // stop like any other exit.
    function playFromList() {
        const sources = DetailController.sourceQueue(-1)
        if (sources.length === 0)
            return
        const first = sources[0]
        PlayerController.stop()
        PlayerController.setMediaContext(DetailController.mediaContext())
        PlayerController.setSourceLabel(first.name, first.title)
        PlayerController.setSourceFile(first.filename || "")
        PlayerController.setSourceQueue(sources.slice(1))
        PlayerController.play(first.url, first.headers)
        player.episodesOpen = false
        player.showControls()
    }

    // Above the control bar, so it never covers the timeline; shown whether
    // or not the controls are, since the end of an episode is exactly when
    // the viewer has stopped touching them.
    SkipSegmentButton {
        z: 11
        anchors.right: parent.right
        anchors.rightMargin: 24
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 120
        shown: player.skipKind !== "" && !player.episodesOpen
        label: player.skipKind === "recap" ? "Skip recap" : "Skip intro"
        onClicked: player.skipSection()
    }

    NextEpisodeCard {
        z: 11
        anchors.right: parent.right
        anchors.rightMargin: 24
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 120
        width: 340
        shown: player.showNext && !player.episodesOpen
        episode: player.nextUp
        busy: !!player.nextUp.videoId && episodePanel.pendingVideoId === player.nextUp.videoId
        message: player._nextRequested ? episodePanel.message : ""
        onClicked: player.playNext()
    }

    // ---- top-right episodes button ----
    // Rides the panel's left edge, so the button that opened it is the one
    // that closes it. Stays up while the panel is open, even with the rest of
    // the controls faded.
    BackButton {
        id: episodesButton
        z: 12
        anchors.top: parent.top
        anchors.topMargin: 16
        x: player.width - 16 - width - episodePanel.reveal * (episodePanel.width + 12)
        blurTarget: video
        glyph: player.episodesOpen ? Icons.x : Icons.episodes
        tooltip: player.episodesOpen ? "Close episodes" : "Episodes (E)"
        opacity: player.hasEpisodes && !player.pip
            && (player.controlsVisible || player.episodesOpen) ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        onClicked: player.toggleEpisodes()
    }

    // ---- top-right format marks ----
    // What the file is -- Dolby Vision, 4K, Dolby Atmos, 5.1 -- read from its
    // own tracks, beside the episodes button (or the corner, without one),
    // and only with the rest of the controls: nothing covers the picture
    // while watching. Never over the title: a narrow window drops marks from
    // the end of the list (channels, then immersive audio, then resolution,
    // then the HDR format) until the rest fit between the title and the
    // right-hand controls.
    Row {
        id: formatMarks
        objectName: "formatMarks"
        z: 12
        spacing: 14
        readonly property real rightEdge: episodesButton.visible
            ? episodesButton.x - 16 : player.width - 20
        // Where the title's own text ends (it is centred), or the back
        // button's edge without a title.
        readonly property real leftEdge: titleText.text.length > 0
            ? player.width / 2 + Math.min(titleColumn.width,
                Math.max(titleText.implicitWidth, labelText.visible ? labelText.implicitWidth : 0)) / 2 + 20
            : backButton.x + backButton.width + 20
        readonly property real room: rightEdge - leftEdge
        x: rightEdge - width
        y: episodesButton.y + (episodesButton.height - height) / 2
        opacity: player.controlsVisible && !player.pip && !player.episodesOpen ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        // Whether marks 0..`index` fit, laid side by side.
        function fits(index: int): bool {
            let total = 0
            for (let i = 0; i <= index && i < marks.count; i++) {
                const mark = marks.itemAt(i) as FormatBadge
                if (!mark)
                    return false
                total += mark.implicitWidth + (i > 0 ? spacing : 0)
            }
            return total <= room
        }

        Repeater {
            id: marks
            model: PlayerController ? PlayerController.mediaBadges : []
            FormatBadge {
                required property var modelData
                required property int index
                format: modelData.format
                label: modelData.label
                detail: modelData.detail
                visible: marks.count > 0 && formatMarks.fits(index)
            }
        }
    }

    // ---- bottom control bar ----
    Rectangle {
        id: controls
        z: 10
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 108
        // The full bar does not fit a tile: a timeline, six buttons and a
        // volume slider inside 480px are unhittable. PiP gets its own row.
        opacity: player.controlsVisible && !player.pip ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        gradient: Gradient {
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.55; color: Qt.rgba(0, 0, 0, 0.72) }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.88) }
        }

        // Swallow clicks so the video click-to-pause underneath doesn't fire.
        MouseArea { anchors.fill: parent; onClicked: (mouse) => { mouse.accepted = true } }

        Column {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 20
            anchors.rightMargin: 20
            anchors.bottomMargin: 10
            spacing: 2

            AppSlider {
                id: timeline
                width: parent.width
                from: 0
                to: Math.max(1, player.dur)
                bufferFraction: player.dur > 0 ? player.bufferedTo / player.dur : 0
                // The file's chapters and the intro/credits edges, however
                // they were learnt: the track splits at each, and hovering
                // names the section under the pointer.
                sections: PlayerController && PlayerController.timelineSections
                    ? PlayerController.timelineSections : []
                hoverText: (value) => player.dur > 0
                    ? player.fmt(value) + player.sectionTitleAt(value) : ""
                // No drag-seeking before the duration is known — a 0..1 range
                // would turn any drag into a seek to the first second.
                enabled: player.dur > 0
                onPressedChanged: {
                    if (!pressed) {
                        PlayerController.seek(value)
                        player.showControls()
                    }
                }
            }

            Item {
                width: parent.width
                height: 44

                Row {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4

                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: PlayerController && PlayerController.paused ? Icons.play : Icons.pause
                        tooltip: PlayerController && PlayerController.paused ? "Play" : "Pause"
                        onClicked: PlayerController.togglePause()
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.replay10
                        tooltip: "Back 10s"
                        onClicked: PlayerController.seekBy(-10)
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.forward10
                        tooltip: "Forward 10s"
                        onClicked: PlayerController.seekBy(10)
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        leftPadding: 8
                        text: player.fmt(timeline.value) + " / " + player.fmt(player.dur)
                        color: Theme.text
                        font.pixelSize: Theme.fontSmall
                    }
                }

                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4

                    AppButton {
                        id: audioBtn
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.audio
                        tooltip: "Audio track"
                        onClicked: {
                            audioMenu.tracks = PlayerController.audioTracks()
                            player.currentAudio = PlayerController.currentAudio()
                            audioMenu.open()
                        }
                        TrackMenu {
                            id: audioMenu
                            currentId: player.currentAudio
                            offEntry: false
                            onPicked: (id) => {
                                player.currentAudio = id
                                PlayerController.selectAudio(id)
                            }
                        }
                    }
                    AppButton {
                        id: subsBtn
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.subtitles
                        tooltip: "Subtitles (G / H to shift timing)"
                        onClicked: {
                            subsMenu.tracks = PlayerController.subtitleTracks()
                            player.currentSub = PlayerController.currentSubtitle()
                            subsMenu.open()
                        }
                        TrackMenu {
                            id: subsMenu
                            currentId: player.currentSub
                            offEntry: true
                            onPicked: (id) => {
                                player.currentSub = id
                                PlayerController.selectSubtitle(id)
                            }
                        }
                        // An addon's file lands seconds after it was picked,
                        // and the addons' list after the menu may have opened:
                        // an open menu follows both.
                        Connections {
                            target: PlayerController
                            function onSubtitleTracksChanged() {
                                if (!subsMenu.visible)
                                    return
                                subsMenu.tracks = PlayerController.subtitleTracks()
                                player.currentSub = PlayerController.currentSubtitle()
                            }
                        }
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: (PlayerController && (PlayerController.muted || PlayerController.volume === 0))
                            ? Icons.volumeOff : Icons.volumeUp
                        tooltip: "Mute"
                        onClicked: PlayerController.toggleMute()
                    }
                    AppSlider {
                        id: volSlider
                        width: 100
                        anchors.verticalCenter: parent.verticalCenter
                        from: 0
                        to: 100
                        value: 100
                        onMoved: PlayerController.setVolume(value)
                        Connections {
                            target: PlayerController
                            function onStateChanged() {
                                if (!volSlider.pressed)
                                    volSlider.value = PlayerController.volume
                            }
                        }
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.pip
                        tooltip: "Picture in picture"
                        // enterPip drops fullscreen itself; this is the same
                        // decision made where the state is read, so the button
                        // cannot ask for a frameless fullscreen window even if
                        // the two ever drift apart.
                        onClicked: {
                            if (player.isFullscreen)
                                player.toggleFullscreen()
                            player.togglePip()
                        }
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: player.isFullscreen ? Icons.fullscreenExit : Icons.fullscreen
                        tooltip: "Fullscreen"
                        onClicked: player.toggleFullscreen()
                    }
                }
            }
        }
    }

    // ---- PiP hover bar ----
    // Four buttons and nothing else: a timeline and a volume slider are not
    // hittable at tile width. No new timer — it rides the same controlsVisible
    // machinery the full bar does, so the pointer revives both the same way.
    Rectangle {
        id: pipBar
        z: 10
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 10
        // Geometry of a pill: the buttons are circles of the bar's height less
        // an even inset on every side, and the same inset pads the ends. That
        // is what makes the first and last button sit INSIDE the round ends
        // rather than beside them, which is how a rounded square in a pill
        // reads — as a block that missed its slot.
        readonly property real inset: 4
        readonly property real buttonSize: height - inset * 2
        width: pipRow.width + inset * 2
        height: 40
        radius: height / 2
        color: Qt.rgba(0, 0, 0, 0.72)
        opacity: player.pip && player.controlsVisible ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        // Swallow presses: the surface underneath starts a system window move
        // on every press, and aiming at a button must not drag the tile.
        MouseArea { anchors.fill: parent; onPressed: (mouse) => { mouse.accepted = true } }

        Row {
            id: pipRow
            anchors.centerIn: parent
            spacing: pipBar.inset

            component PipButton: AppButton {
                ghost: true
                iconSize: 18
                // Square, so the wash is a circle rather than a stadium, and
                // round, so it follows the pill it sits in.
                implicitWidth: pipBar.buttonSize
                implicitHeight: pipBar.buttonSize
                padding: 0
                radius: width / 2
            }

            PipButton {
                iconGlyph: Icons.replay10
                onClicked: PlayerController.seekBy(-10)
            }
            PipButton {
                iconGlyph: PlayerController && PlayerController.paused ? Icons.play : Icons.pause
                onClicked: PlayerController.togglePause()
            }
            PipButton {
                iconGlyph: Icons.forward10
                onClicked: PlayerController.seekBy(10)
            }
            PipButton {
                iconGlyph: Icons.pipExit
                onClicked: player.leavePip()
            }
        }
    }

    // ---- PiP resize grip ----
    // The resize is done in app coordinates, not handed to the compositor with
    // startSystemResize. A platform-driven resize owns the frame for the length
    // of the drag and decides for itself whether to honour the height the
    // aspect lock asks for, which is what made the tile jump vertically on some
    // drags and not others. Dragging it here means one writer for both
    // dimensions and a tile that is always exactly the shape of the video.
    //
    // The reference point is in SCENE coordinates: the grip is anchored to the
    // right edge, so it slides out from under the pointer as the window grows,
    // and a delta measured against the item would cancel itself out. The
    // window's top-left does not move while its right edge is dragged, so scene
    // coordinates track the pointer on screen.
    Item {
        id: pipGrip
        z: 11
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        width: 22
        height: 22
        opacity: player.pip && player.controlsVisible ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        Rectangle {
            anchors.centerIn: parent
            width: 15; height: 2
            radius: 1
            rotation: -45
            color: Qt.rgba(1, 1, 1, 0.6)
        }
        Rectangle {
            anchors.centerIn: parent
            anchors.horizontalCenterOffset: 4
            anchors.verticalCenterOffset: 4
            width: 7; height: 2
            radius: 1
            rotation: -45
            color: Qt.rgba(1, 1, 1, 0.6)
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.SizeFDiagCursor
            property real startSceneX: 0
            property real startWidth: 0
            onPressed: (mouse) => {
                if (!Window.window)
                    return
                startSceneX = mapToItem(null, mouse.x, mouse.y).x
                startWidth = Window.window.width
            }
            onPositionChanged: (mouse) => {
                if (!pressed || !Window.window)
                    return
                var moved = mapToItem(null, mouse.x, mouse.y).x - startSceneX
                player.pipResizeRequested(startWidth + moved)
            }
        }
    }
}
