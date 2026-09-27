pragma ComponentBehavior: Bound
import QtQuick
import Gravitas
import "components"

// First-run wizard. Main.qml loads this as a full-screen overlay while
// OnboardingController.active; Finish and Skip both run the exit animation
// and then call complete(), which persists the flag — the Loader tears the
// overlay down only after the fade has finished.
FocusScope {
    id: root
    focus: true

    property int step: 0
    readonly property int stepCount: 4
    readonly property bool exiting: exitAnim.running

    function next() {
        if (exiting)
            return
        if (step < stepCount - 1)
            step += 1
        else
            finish()
    }
    function back() {
        if (!exiting && step > 0)
            step -= 1
    }
    function finish() {
        if (!exiting)
            exitAnim.start()
    }

    // Fade the whole overlay away with a slight zoom, THEN flip the flag.
    // complete() unloads the Loader instantly, so it must come last.
    SequentialAnimation {
        id: exitAnim
        ParallelAnimation {
            NumberAnimation { target: root; property: "opacity"; to: 0; duration: 280; easing.type: Easing.InCubic }
            NumberAnimation { target: root; property: "scale"; to: 1.04; duration: 280; easing.type: Easing.InCubic }
        }
        ScriptAction { script: OnboardingController.complete() }
    }

    // Left/Right page through, Enter advances, Esc skips. While the addon
    // field has focus, Enter belongs to it (its onAccepted submits the URL)
    // — swallow it here so a submit never ALSO turns the page.
    Keys.onPressed: (event) => {
        const enter = event.key === Qt.Key_Return || event.key === Qt.Key_Enter
        if (enter && addonField.activeFocus) {
            event.accepted = true
        } else if (event.key === Qt.Key_Right || enter) {
            next(); event.accepted = true
        } else if (event.key === Qt.Key_Left) {
            back(); event.accepted = true
        } else if (event.key === Qt.Key_Escape) {
            finish(); event.accepted = true
        }
    }

    Rectangle { anchors.fill: parent; color: Theme.bg }
    AmbientBackground { anchors.fill: parent }

    // Swallow every press and scroll so the app underneath stays inert while
    // the wizard is up (the window's own TapHandlers sit below this overlay).
    MouseArea {
        anchors.fill: parent
        onWheel: (wheel) => wheel.accepted = true
    }

    AppButton {
        text: "Skip"
        ghost: true
        visible: root.step < root.stepCount - 1
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.margins: 20
        onClicked: root.finish()
    }

    Item {
        id: content
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 560)
        height: parent.height

        // --- pages ---
        Item {
            id: pages
            anchors.fill: parent
            anchors.bottomMargin: 96

            // Welcome
            Item {
                readonly property bool isCurrent: root.step === 0
                anchors.fill: parent
                opacity: isCurrent ? 1 : 0
                x: isCurrent ? 0 : (root.step > 0 ? -40 : 40)
                visible: opacity > 0
                enabled: isCurrent
                Behavior on opacity { NumberAnimation { duration: 220 } }
                Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutCubic } }

                Column {
                    anchors.centerIn: parent
                    spacing: Theme.spacing * 2
                    Image {
                        id: logo
                        source: "assets/gravitas.png"
                        width: 104; height: 104
                        mipmap: true
                        anchors.horizontalCenter: parent.horizontalCenter
                        opacity: 0
                    }
                    Text {
                        id: welcomeTitle
                        text: "Welcome to Gravitas"
                        color: Theme.text
                        font.pixelSize: 34
                        font.bold: true
                        anchors.horizontalCenter: parent.horizontalCenter
                        opacity: 0
                    }
                    Text {
                        id: welcomeTag
                        text: "A minimal, no-nonsense home for your movies and series."
                        color: Theme.textDim
                        font.pixelSize: Theme.fontTitle
                        width: content.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                        opacity: 0
                    }
                }

                // One-time staggered entrance: logo settles in, then the
                // title and tagline follow. Runs once at overlay creation —
                // navigating back to this page keeps everything visible.
                SequentialAnimation {
                    running: true
                    PauseAnimation { duration: 150 }
                    ParallelAnimation {
                        NumberAnimation { target: logo; property: "opacity"; to: 1; duration: 420; easing.type: Easing.OutCubic }
                        NumberAnimation { target: logo; property: "scale"; from: 0.7; to: 1; duration: 480; easing.type: Easing.OutBack }
                    }
                    NumberAnimation { target: welcomeTitle; property: "opacity"; to: 1; duration: 320; easing.type: Easing.OutCubic }
                    NumberAnimation { target: welcomeTag; property: "opacity"; to: 1; duration: 320; easing.type: Easing.OutCubic }
                }
            }

            // Addons
            Item {
                readonly property bool isCurrent: root.step === 1
                anchors.fill: parent
                opacity: isCurrent ? 1 : 0
                x: isCurrent ? 0 : (root.step > 1 ? -40 : 40)
                visible: opacity > 0
                enabled: isCurrent
                Behavior on opacity { NumberAnimation { duration: 220 } }
                Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutCubic } }

                Column {
                    anchors.centerIn: parent
                    width: content.width
                    spacing: Theme.spacing * 2
                    Text {
                        text: "Your content comes from addons"
                        color: Theme.text
                        font.pixelSize: 26
                        font.bold: true
                        width: parent.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: "Gravitas speaks the Stremio addon protocol. Cinemeta is already installed for movie and series metadata. Paste any addon's manifest URL to add more sources."
                        color: Theme.textDim
                        font.pixelSize: Theme.fontBody
                        width: parent.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Row {
                        spacing: Theme.spacing
                        anchors.horizontalCenter: parent.horizontalCenter
                        AppTextField {
                            id: addonField
                            width: content.width - addButton.width - spinner.width - Theme.spacing * 2
                            placeholderText: "https://…/manifest.json"
                            onAccepted: addButton.submit()
                        }
                        AppButton {
                            id: addButton
                            text: "Add"
                            tone: "accent"
                            // Stays enabled (so the pointer cursor shows) and
                            // validates on activation instead: a manifest URL
                            // must be absolute http(s), everything else gets
                            // the inline error rather than a doomed request.
                            enabled: !AddonController.installing
                            function submit() {
                                if (!enabled)
                                    return
                                const url = addonField.text.trim()
                                if (!/^https?:\/\/.+/i.test(url)) {
                                    addonStatus.isError = true
                                    addonStatus.text = url.length === 0
                                        ? "Paste an addon manifest URL first."
                                        : "That doesn't look like an addon URL: it should start with https://"
                                    return
                                }
                                addonStatus.text = ""
                                AddonController.addAddon(url)
                            }
                            onClicked: submit()
                        }
                        AppSpinner {
                            id: spinner
                            width: Theme.controlHeight; height: Theme.controlHeight
                            running: AddonController.installing
                        }
                    }
                    Text {
                        id: addonStatus
                        property bool isError: false
                        text: ""
                        visible: text.length > 0
                        color: isError ? Theme.negative : Theme.positive
                        font.pixelSize: Theme.fontSmall
                        width: parent.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Text {
                        text: "Entirely optional. Addons can be added or removed in Settings at any time."
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        width: parent.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                // Inline feedback instead of the global toast: the toast pops
                // under this overlay and would go unseen.
                Connections {
                    target: AddonController
                    enabled: root.step === 1
                    function onAddonInstalled(name) {
                        addonStatus.isError = false
                        addonStatus.text = "Installed: " + name
                        addonField.text = ""
                    }
                    function onErrorOccurred(msg) {
                        addonStatus.isError = true
                        addonStatus.text = msg
                    }
                }
            }

            // Features
            Item {
                readonly property bool isCurrent: root.step === 2
                anchors.fill: parent
                opacity: isCurrent ? 1 : 0
                x: isCurrent ? 0 : (root.step > 2 ? -40 : 40)
                visible: opacity > 0
                enabled: isCurrent
                Behavior on opacity { NumberAnimation { duration: 220 } }
                Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutCubic } }

                Column {
                    anchors.centerIn: parent
                    width: content.width
                    spacing: Theme.spacing * 3
                    Text {
                        text: "Built to stay out of your way"
                        color: Theme.text
                        font.pixelSize: 26
                        font.bold: true
                        width: parent.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                    Column {
                        width: parent.width
                        spacing: Theme.spacing * 2
                        Repeater {
                            model: [
                                { glyph: Icons.search, title: "Search everything", line: "One search box across every installed addon's catalog." },
                                { glyph: Icons.bookmark, title: "Watchlist and progress", line: "Save titles for later; progress bars and resume follow you across the app." },
                                { glyph: Icons.gear, title: "Make it yours", line: "Trakt sync, subtitle styling and metadata keys live in Settings." }
                            ]
                            delegate: Row {
                                id: feature
                                required property var modelData
                                spacing: Theme.spacing * 2
                                width: parent.width
                                Rectangle {
                                    width: 44; height: 44
                                    radius: Theme.radius
                                    color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.14)
                                    anchors.verticalCenter: parent.verticalCenter
                                    AppIcon {
                                        anchors.centerIn: parent
                                        glyph: feature.modelData.glyph
                                        color: Theme.accent
                                        font.pixelSize: 22
                                    }
                                }
                                Column {
                                    width: parent.width - 44 - Theme.spacing * 2
                                    spacing: 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text {
                                        text: feature.modelData.title
                                        color: Theme.text
                                        font.pixelSize: Theme.fontBody
                                        font.bold: true
                                    }
                                    Text {
                                        text: feature.modelData.line
                                        color: Theme.textDim
                                        font.pixelSize: Theme.fontSmall
                                        width: parent.width
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Done
            Item {
                id: donePage
                readonly property bool isCurrent: root.step === 3
                anchors.fill: parent
                opacity: isCurrent ? 1 : 0
                x: isCurrent ? 0 : 40
                visible: opacity > 0
                enabled: isCurrent
                Behavior on opacity { NumberAnimation { duration: 220 } }
                Behavior on x { NumberAnimation { duration: 340; easing.type: Easing.OutCubic } }

                Column {
                    anchors.centerIn: parent
                    spacing: Theme.spacing * 2
                    Rectangle {
                        width: 88; height: 88
                        radius: width / 2
                        color: Qt.rgba(Theme.positive.r, Theme.positive.g, Theme.positive.b, 0.16)
                        anchors.horizontalCenter: parent.horizontalCenter
                        // Pop in each time the page becomes current — the
                        // payoff beat of the whole wizard.
                        scale: donePage.isCurrent ? 1 : 0.6
                        Behavior on scale { NumberAnimation { duration: 420; easing.type: Easing.OutBack } }
                        AppIcon {
                            anchors.centerIn: parent
                            glyph: Icons.check
                            color: Theme.positive
                            font.pixelSize: 44
                        }
                    }
                    Text {
                        text: "You're all set"
                        color: Theme.text
                        font.pixelSize: 30
                        font.bold: true
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                    Text {
                        text: "Your catalog is loading behind this screen. Enjoy."
                        color: Theme.textDim
                        font.pixelSize: Theme.fontBody
                        width: content.width
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }

        // --- navigation: back / progress dots / continue ---
        Item {
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 36
            width: parent.width
            height: Theme.controlHeight

            AppButton {
                text: "Back"
                ghost: true
                iconGlyph: Icons.arrowLeft
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                opacity: root.step > 0 ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: Theme.durFast } }
                onClicked: root.back()
            }

            Row {
                spacing: Theme.spacing
                anchors.centerIn: parent
                Repeater {
                    model: root.stepCount
                    delegate: Rectangle {
                        required property int index
                        readonly property bool current: index === root.step
                        width: current ? 24 : 8
                        height: 8
                        radius: 4
                        color: current ? Theme.accent : Theme.borderStrong
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on width { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                        Behavior on color { ColorAnimation { duration: 260 } }
                    }
                }
            }

            AppButton {
                text: root.step === root.stepCount - 1 ? "Start exploring" : "Continue"
                tone: "accent"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                onClicked: root.next()
            }
        }
    }

    // Ease the whole overlay in on first paint so launch doesn't hard-cut
    // from the window background to the wizard.
    NumberAnimation on opacity { from: 0; to: 1; duration: 320; easing.type: Easing.OutCubic }
}
