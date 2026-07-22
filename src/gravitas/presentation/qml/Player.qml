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
    // Smooth exit: fade the page and the audio together, THEN stop and pop —
    // a bare stop() cuts sound and freezes the frame mid-scene.
    property real _restoreVol: 100
    function leave() {
        if (exitAnim.running)
            return
        if (player.isFullscreen)
            Window.window.visibility = Window.Windowed
        player._restoreVol = playerController.volume
        volFader.v = playerController.volume
        exitAnim.start()
    }
    QtObject {
        id: volFader
        property real v: 100
        onVChanged: playerController.setVolume(v)
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
        playerController.stop()
        // Restore the user's volume silently for the next playback.
        playerController.setVolume(player._restoreVol)
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
        visible: opacity > 0
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
        target: playerController
        function onResumed(position) {
            resumeLabel.text = "Resumed from " + player.fmt(position)
            resumeFade.restart()
        }
    }

    Component.onCompleted: {
        pageFade.restart()
        if (player.url.length)
            playerController.play(player.url, player.headers)
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
        && !subsMenu.visible && !audioMenu.visible

    function kbTogglePause() {
        player.showControls()
        playerController.togglePause()
        // Flash the NEW state, YouTube-style: pausing shows the pause bars.
        osd.flash(playerController.paused ? Icons.pause : Icons.play)
    }
    function kbSeek(delta) {
        player.showControls()
        playerController.seekBy(delta)
        if (delta <= -60) osd.flash(Icons.fastRewind, "60s")
        else if (delta < 0) osd.flash(Icons.replay10)
        else if (delta >= 60) osd.flash(Icons.fastForward, "60s")
        else osd.flash(Icons.forward10)
    }
    function kbVolume(delta) {
        player.showControls()
        playerController.setVolume(
            Math.max(0, Math.min(100, playerController.volume + delta)))
        osd.flash(playerController.volume > 0 ? Icons.volumeUp : Icons.volumeOff,
                  Math.round(playerController.volume) + "%")
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
        enabled: player.hotkeysOn
        sequence: "M"
        autoRepeat: false
        onActivated: {
            player.showControls()
            playerController.toggleMute()
            osd.flash(playerController.muted ? Icons.volumeOff : Icons.volumeUp,
                      playerController.muted ? "Muted" : Math.round(playerController.volume) + "%")
        }
    }
    Shortcut {
        enabled: player.hotkeysOn
        sequence: "Escape"
        autoRepeat: false
        // Window.window is an Item-attached property; on a Shortcut it is
        // null, so going through the root item's function is the only way.
        onActivated: {
            if (player.isFullscreen)
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
                    playerController.seek(player.dur * index / 10)
                }
            }
        }
    }

    Rectangle { anchors.fill: parent; color: "black" }

    // MpvVideo is registered at runtime in main.py (qmlRegisterType), so the
    // linter has no type info for it and the import/type errors are false.
    // qmllint disable import unresolved-type
    MpvVideo {
        id: video
        anchors.fill: parent
        Component.onCompleted: playerController.attachVideo(video)
    }
    // qmllint enable import unresolved-type

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
            player.dur = playerController.duration
            player.buffering = playerController.isLoading()
            player.bufferedTo = playerController.bufferedTo()
            if (!timeline.pressed)
                timeline.value = playerController.position()
        }
    }

    // Single click pauses only after the double-click window passes —
    // otherwise a double-click (fullscreen) also toggles pause twice.
    Timer {
        id: singleClickTimer
        interval: 220
        onTriggered: { playerController.togglePause(); player.showControls() }
    }
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: player.controlsVisible ? Qt.ArrowCursor : Qt.BlankCursor
        onPositionChanged: player.showControls()
        onClicked: singleClickTimer.restart()
        onDoubleClicked: {
            singleClickTimer.stop()
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
        width: 84
        height: 84
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
            radius: 18
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
                - (osd.hasLabel ? 9 : 0)
            glyph: osd.glyph
            font.pixelSize: osd.hasLabel ? 32 : 40
            color: "white"
        }
        FontMetrics { id: osdIconMetrics; font: osdIcon.font }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 12
            visible: osd.hasLabel
            text: osd.label
            color: "white"
            font.pixelSize: Theme.fontSmall
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
        z: 5
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.45)
        opacity: player.showLoader ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }

        GravitasLoader {
            anchors.centerIn: parent
            running: parent.visible
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
        opacity: player.controlsVisible
            && playerController && playerController.mediaTitle ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.88) }
            GradientStop { position: 0.45; color: Qt.rgba(0, 0, 0, 0.72) }
            GradientStop { position: 1.0; color: "transparent" }
        }

        Column {
            // Clear of the back button on both sides so a long title stays
            // centered instead of sliding under it.
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 20
            width: parent.width - 176
            spacing: 2

            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                text: (playerController && playerController.mediaTitle) || ""
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.bold: true
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
                visible: !!(playerController && playerController.mediaLabel)
                text: (playerController && playerController.mediaLabel) || ""
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
        }
    }

    // ---- top-left back button ----
    BackButton {
        z: 10
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 16
        // video is MpvVideo (runtime-registered); the linter sees it as unknown.
        // qmllint disable incompatible-type
        blurTarget: video
        // qmllint enable incompatible-type
        opacity: player.controlsVisible ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
        onClicked: player.leave()
    }

    // ---- bottom control bar ----
    Rectangle {
        id: controls
        z: 10
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 108
        opacity: player.controlsVisible ? 1 : 0
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
                // No drag-seeking before the duration is known — a 0..1 range
                // would turn any drag into a seek to the first second.
                enabled: player.dur > 0
                onPressedChanged: {
                    if (!pressed) {
                        playerController.seek(value)
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
                        iconGlyph: playerController && playerController.paused ? Icons.play : Icons.pause
                        tooltip: playerController && playerController.paused ? "Play" : "Pause"
                        onClicked: playerController.togglePause()
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.replay10
                        tooltip: "Back 10s"
                        onClicked: playerController.seekBy(-10)
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.forward10
                        tooltip: "Forward 10s"
                        onClicked: playerController.seekBy(10)
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
                            audioMenu.tracks = playerController.audioTracks()
                            player.currentAudio = playerController.currentAudio()
                            audioMenu.open()
                        }
                        TrackMenu {
                            id: audioMenu
                            currentId: player.currentAudio
                            offEntry: false
                            onPicked: (id) => {
                                player.currentAudio = id
                                playerController.selectAudio(id)
                            }
                        }
                    }
                    AppButton {
                        id: subsBtn
                        ghost: true
                        iconSize: 24
                        iconGlyph: Icons.subtitles
                        tooltip: "Subtitles"
                        onClicked: {
                            subsMenu.tracks = playerController.subtitleTracks()
                            player.currentSub = playerController.currentSubtitle()
                            subsMenu.open()
                        }
                        TrackMenu {
                            id: subsMenu
                            currentId: player.currentSub
                            offEntry: true
                            onPicked: (id) => {
                                player.currentSub = id
                                playerController.selectSubtitle(id)
                            }
                        }
                    }
                    AppButton {
                        ghost: true
                        iconSize: 24
                        iconGlyph: (playerController && (playerController.muted || playerController.volume === 0))
                            ? Icons.volumeOff : Icons.volumeUp
                        tooltip: "Mute"
                        onClicked: playerController.toggleMute()
                    }
                    AppSlider {
                        id: volSlider
                        width: 100
                        anchors.verticalCenter: parent.verticalCenter
                        from: 0
                        to: 100
                        value: 100
                        onMoved: playerController.setVolume(value)
                        Connections {
                            target: playerController
                            function onStateChanged() {
                                if (!volSlider.pressed)
                                    volSlider.value = playerController.volume
                            }
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
}
