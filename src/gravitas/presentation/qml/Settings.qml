import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: settings
    objectName: "settingsPage"
    signal back()

    Flickable {
        id: page
        anchors.fill: parent
        contentHeight: column.height + 104
        // Content rests below the floating top bar but scrolls under it.
        topMargin: 80
        // A Flickable does not move contentY when a topMargin is set on it:
        // it stays at 0, which is 80px INTO the content — the page opened
        // pre-scrolled past the title row. The rest position is -topMargin.
        Component.onCompleted: contentY = -topMargin
        clip: true
        ScrollBar.vertical: AppScrollBar {}

        Column {
            id: column
            width: Math.min(page.width - 48, 720)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 20

            Row {
                spacing: 12
                BackButton {
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: settings.back()
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Settings"
                    color: Theme.text
                    font.pixelSize: Theme.fontTitle
                    font.bold: true
                }
            }

            SettingsCard {
                width: parent.width
                title: "Addons"
                caption: "Stremio-compatible addon manifests"

                Row {
                    width: parent.width
                    spacing: 8
                    // Manifest fetch + first catalog load take visible
                    // seconds; the row locks and a spinner slides in so the
                    // click reads as "working", not "ignored".
                    readonly property bool busy: addonController && addonController.installing
                    AppTextField {
                        id: urlField
                        width: parent.width - addButton.width - addSpinner.width
                            - parent.spacing * (addSpinner.width > 0 ? 2 : 1)
                        enabled: !parent.busy
                        opacity: enabled ? 1 : 0.6
                        Behavior on opacity { NumberAnimation { duration: Theme.durFast } }
                        placeholderText: "Addon manifest URL…"
                        onAccepted: addButton.clicked()
                    }
                    AppSpinner {
                        id: addSpinner
                        anchors.verticalCenter: parent.verticalCenter
                        // Slide open from zero width so the field glides
                        // aside instead of jumping.
                        width: parent.busy ? 18 : 0
                        height: 18
                        running: parent.busy
                        opacity: parent.busy ? 1 : 0
                        Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
                        Behavior on opacity { NumberAnimation { duration: Theme.durFast } }
                    }
                    AppButton {
                        id: addButton
                        text: parent.busy ? "Adding…" : "Add"
                        tone: "positive"
                        enabled: !parent.busy
                        onClicked: {
                            if (urlField.text.trim().length === 0)
                                return
                            addonController.addAddon(urlField.text)
                            urlField.text = ""
                        }
                    }
                }

                // Bootstrap is still restoring addons when Settings opens
                // early; a spinner beats an empty card that pops full.
                Row {
                    spacing: 10
                    visible: settingsController && settingsController.addonsLoading
                    AppSpinner {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 20; height: 20
                        running: parent.visible
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Loading addons…"
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                    }
                }

                ListView {
                    width: parent.width
                    height: Math.min(contentHeight, 320)
                    model: addonListModel
                    interactive: contentHeight > height
                    clip: true
                    spacing: 6
                    ScrollBar.vertical: AppScrollBar {}
                    // The list lands as one model reset when bootstrap
                    // finishes; fade the rows in with a slight rise so the
                    // handover from the spinner reads as motion, not a pop.
                    populate: Transition {
                        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durMed * 2; easing.type: Easing.OutCubic }
                        NumberAnimation { property: "y"; from: 12; duration: Theme.durMed * 2; easing.type: Easing.OutCubic }
                    }
                    delegate: Rectangle {
                        id: addonRow
                        width: ListView.view.width
                        height: 56
                        radius: Theme.radius
                        color: rowHover.hovered ? Theme.surfaceHover : Theme.bg
                        Behavior on color { ColorAnimation { duration: Theme.durFast } }
                        required property string name
                        required property string addonId
                        required property bool removable
                        required property string version
                        HoverHandler { id: rowHover }

                        Row {
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 12

                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 32; height: 32; radius: 16
                                color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
                                Text {
                                    anchors.centerIn: parent
                                    text: addonRow.name.length > 0 ? addonRow.name.charAt(0).toUpperCase() : "?"
                                    color: Theme.accentHover
                                    font.pixelSize: Theme.fontBody
                                    font.bold: true
                                }
                            }
                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 2
                                Text {
                                    text: addonRow.name
                                    color: Theme.text
                                    font.pixelSize: Theme.fontBody
                                }
                                Text {
                                    text: "v" + addonRow.version + " · " + addonRow.addonId
                                    color: Theme.textDim
                                    font.pixelSize: Theme.fontSmall
                                }
                            }
                        }

                        // Built-in chip for protected addons; trash for the rest.
                        Rectangle {
                            visible: !addonRow.removable
                            anchors.right: parent.right
                            anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            width: builtinLabel.implicitWidth + 16
                            height: 22
                            radius: 11
                            color: "transparent"
                            border.width: 1
                            border.color: Theme.borderStrong
                            Text {
                                id: builtinLabel
                                anchors.centerIn: parent
                                text: "Built-in"
                                color: Theme.textDim
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                        AppButton {
                            ghost: true
                            iconGlyph: Icons.trash
                            tooltip: "Remove addon"
                            tone: "negative"
                            visible: addonRow.removable
                            anchors.right: parent.right
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: settingsController.removeAddon(addonRow.addonId)
                        }
                    }
                }
            }

            SettingsCard {
                width: parent.width
                title: "Metadata"
                caption: "External metadata providers"
                enterDelay: 60

                Row {
                    width: parent.width
                    spacing: 8
                    // Bindings here guard the controller OBJECT, not just its
                    // property: context properties go null at engine teardown and
                    // every live binding re-evaluates on the way down, so a bare
                    // settingsController.tmdbKey throws a TypeError on each exit.
                    AppTextField {
                        width: parent.width - savedTick.width - parent.spacing
                        placeholderText: "TMDB API key (optional — needed to open TVDB links)"
                        text: settingsController ? settingsController.tmdbKey : ""
                        onEditingFinished: {
                            settingsController.setTmdbKey(text)
                            savedFade.restart()
                        }
                    }
                    Text {
                        id: savedTick
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Saved ✓"
                        color: Theme.positive
                        font.pixelSize: Theme.fontSmall
                        opacity: 0
                        SequentialAnimation {
                            id: savedFade
                            NumberAnimation { target: savedTick; property: "opacity"; to: 1; duration: Theme.durFast }
                            PauseAnimation { duration: 1400 }
                            NumberAnimation { target: savedTick; property: "opacity"; to: 0; duration: Theme.durMed }
                        }
                    }
                }

                Row {
                    width: parent.width
                    spacing: 8
                    AppTextField {
                        width: parent.width - mdbSavedTick.width - parent.spacing
                        placeholderText: "MDBList API key (optional — enables Rotten Tomatoes + Letterboxd)"
                        text: settingsController ? settingsController.mdblistKey : ""
                        onEditingFinished: {
                            settingsController.setMdblistKey(text)
                            mdbSavedFade.restart()
                        }
                    }
                    Text {
                        id: mdbSavedTick
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Saved ✓"
                        color: Theme.positive
                        font.pixelSize: Theme.fontSmall
                        opacity: 0
                        SequentialAnimation {
                            id: mdbSavedFade
                            NumberAnimation { target: mdbSavedTick; property: "opacity"; to: 1; duration: Theme.durFast }
                            PauseAnimation { duration: 1400 }
                            NumberAnimation { target: mdbSavedTick; property: "opacity"; to: 0; duration: Theme.durMed }
                        }
                    }
                }
            }

            SettingsCard {
                width: parent.width
                title: "Integrations"
                enterDelay: 75

                // One row, Stremio-style: icon, label, and a single action
                // pill. Authenticate opens trakt.tv in the browser with the
                // device code pre-filled; the row flips as the poll resolves.
                Item {
                    width: parent.width
                    height: 56

                    Row {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 14

                        Image {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 40; height: 40
                            source: "assets/trakt.png"
                            sourceSize.width: 80
                            fillMode: Image.PreserveAspectFit
                        }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                text: "Trakt Scrobbling"
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                                font.bold: true
                            }
                            Text {
                                text: {
                                    if (!traktController) return ""
                                    if (traktController.authenticated)
                                        return "Connected"
                                            + (traktController.username.length > 0
                                                ? " as " + traktController.username : "")
                                    if (traktController.authInProgress)
                                        return "Approve in your browser — code "
                                            + traktController.userCode
                                    return "Sync what you watch to your Trakt profile"
                                }
                                color: traktController && traktController.authenticated
                                    ? Theme.positive : Theme.textDim
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                    }

                    Row {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 8

                        AppSpinner {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 18; height: 18
                            running: traktController
                                && (traktController.authInProgress || traktController.syncing)
                            visible: running
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: traktController && traktController.authenticated
                            enabled: !(traktController && traktController.syncing)
                            text: traktController && traktController.syncing
                                ? "Syncing…" : "Sync progress"
                            ghost: true
                            tooltip: "Pull unfinished playback from Trakt into Continue Watching"
                            onClicked: traktController.syncNow()
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: traktController
                                && !traktController.authenticated
                                && !traktController.authInProgress
                            text: "Authenticate"
                            onClicked: traktController.startAuth()
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: traktController && traktController.authInProgress
                            text: "Cancel"
                            ghost: true
                            onClicked: traktController.cancelAuth()
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: traktController && traktController.authenticated
                            text: "Log out"
                            tone: "negative"
                            ghost: true
                            onClicked: traktController.logout()
                        }
                    }
                }

                // Mirror policy. Off never blocks the local action — the
                // sync always prefers local forgets/marks either way; these
                // only decide whether Trakt is told about them.
                Row {
                    visible: traktController && traktController.authenticated
                    spacing: 24
                    AppCheckBox {
                        checked: traktController ? traktController.syncForgets : true
                        label: "Sync forgets to Trakt"
                        tooltip: "Forgetting progress here also clears it on your Trakt account"
                        onToggled: (value) => traktController.setSyncForgets(value)
                    }
                    AppCheckBox {
                        checked: traktController ? traktController.syncWatched : true
                        label: "Sync watched to Trakt"
                        tooltip: "Marking something watched here also adds it to your Trakt history"
                        onToggled: (value) => traktController.setSyncWatched(value)
                    }
                }
            }

            SettingsCard {
                width: parent.width
                title: "Subtitles"
                caption: "Changes apply to the player instantly"
                enterDelay: 90

                // Live preview over a fake scene backdrop.
                Rectangle {
                    width: parent.width
                    height: 150
                    radius: Theme.radius
                    clip: true
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: "#2c3648" }
                        GradientStop { position: 0.6; color: "#171c26" }
                        GradientStop { position: 1.0; color: "#0b0d12" }
                    }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 14
                        width: previewText.implicitWidth + 20
                        height: previewText.implicitHeight + 10
                        radius: 4
                        color: Qt.rgba(0, 0, 0,
                            (settingsController && settingsController.subBackOpacity !== undefined
                                ? settingsController.subBackOpacity : 0) / 100)
                        Text {
                            id: previewText
                            anchors.centerIn: parent
                            text: "This is what subtitles will look like."
                            color: settingsController && settingsController.subColor
                                ? settingsController.subColor : "#FFFFFF"
                            // Preview at ~45% of mpv's rendering scale so the
                            // strip stays proportionate.
                            font.pixelSize: Math.round(
                                ((settingsController && settingsController.subFontSize) || 55) * 0.45)
                            font.bold: settingsController && settingsController.subBold ? true : false
                            style: settingsController && settingsController.subBorderSize > 0
                                ? Text.Outline : Text.Normal
                            styleColor: "black"
                        }
                    }
                }

                // External updates (reset) re-sync the sliders; user drags
                // write through onMoved.
                Connections {
                    target: settingsController
                    function onSubtitleStyleChanged() {
                        if (!sizeSlider.pressed) sizeSlider.value = settingsController.subFontSize
                        if (!outlineSlider.pressed) outlineSlider.value = settingsController.subBorderSize
                        if (!backSlider.pressed) backSlider.value = settingsController.subBackOpacity
                    }
                }

                Row {
                    width: parent.width
                    spacing: 12
                    Text {
                        width: 96
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Size"
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                    }
                    AppSlider {
                        id: sizeSlider
                        width: parent.width - 96 - 12
                        from: 20; to: 100; stepSize: 1
                        value: settingsController && settingsController.subFontSize !== undefined
                            ? settingsController.subFontSize : 55
                        onMoved: settingsController.setSubFontSize(value)
                    }
                }
                Row {
                    width: parent.width
                    spacing: 12
                    Text {
                        width: 96
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Outline"
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                    }
                    AppSlider {
                        id: outlineSlider
                        width: parent.width - 96 - 12
                        from: 0; to: 8; stepSize: 1
                        value: settingsController && settingsController.subBorderSize !== undefined
                            ? settingsController.subBorderSize : 3
                        onMoved: settingsController.setSubBorderSize(value)
                    }
                }
                Row {
                    width: parent.width
                    spacing: 12
                    Text {
                        width: 96
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Background"
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                    }
                    AppSlider {
                        id: backSlider
                        width: parent.width - 96 - 12
                        from: 0; to: 100; stepSize: 5
                        value: settingsController && settingsController.subBackOpacity !== undefined
                            ? settingsController.subBackOpacity : 0
                        onMoved: settingsController.setSubBackOpacity(value)
                    }
                }
                Row {
                    width: parent.width
                    spacing: 12
                    Text {
                        width: 96
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Color"
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                    }
                    Row {
                        spacing: 8
                        anchors.verticalCenter: parent.verticalCenter
                        Repeater {
                            model: ["#FFFFFF", "#FFE400", "#00E5FF", "#7CFF6B"]
                            Rectangle {
                                required property string modelData
                                width: 26; height: 26; radius: 13
                                color: modelData
                                border.width: settingsController && settingsController.subColor === modelData ? 3 : 1
                                border.color: settingsController && settingsController.subColor === modelData
                                    ? Theme.accent : Theme.borderStrong
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: settingsController.setSubColor(modelData) }
                            }
                        }
                    }
                    Item { width: 24; height: 1 }
                    AppButton {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Bold"
                        ghost: true
                        selected: settingsController && settingsController.subBold ? true : false
                        onClicked: settingsController.setSubBold(!settingsController.subBold)
                    }
                    AppButton {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Reset"
                        ghost: true
                        onClicked: settingsController.resetSubtitleStyle()
                    }
                }
            }

            SettingsCard {
                width: parent.width
                title: "Watch progress"
                caption: "Saved playback positions, kept on this device only"
                enterDelay: 120

                // The list is only correct while the page is open; repopulate
                // on entry rather than keeping it live for a hidden page.
                Component.onCompleted: progressController.refreshWatched()

                Text {
                    visible: watchedList.count === 0
                    text: "Nothing in progress yet. Positions are saved automatically after 30 seconds of playback."
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                    width: parent.width
                }

                ListView {
                    id: watchedList
                    width: parent.width
                    height: Math.min(contentHeight, 320)
                    visible: count > 0
                    model: watchedListModel
                    interactive: contentHeight > height
                    clip: true
                    spacing: 6
                    ScrollBar.vertical: AppScrollBar {}
                    delegate: Rectangle {
                        id: watchedRow
                        width: ListView.view.width
                        height: 56
                        radius: Theme.radius
                        color: watchedHover.hovered ? Theme.surfaceHover : Theme.bg
                        Behavior on color { ColorAnimation { duration: Theme.durFast } }
                        required property string mediaId
                        required property string name
                        required property string poster
                        required property string label
                        required property real progressFraction
                        HoverHandler { id: watchedHover }

                        Row {
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            anchors.right: forgetButton.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 12

                            Item {
                                id: rowThumb
                                anchors.verticalCenter: parent.verticalCenter
                                width: 28; height: 40
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 4
                                    color: Theme.surfaceHover
                                    visible: thumb.status !== Image.Ready
                                }
                                Image {
                                    id: thumb
                                    anchors.fill: parent
                                    source: Img.sized(watchedRow.poster, 80)
                                    sourceSize.width: 80
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true
                                }
                            }
                            Column {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 2
                                // Derived, not 40: that was the thumbnail's width
                                // plus the Row's spacing restated as a magic
                                // number, so resizing the thumbnail silently
                                // broke the text's eliding.
                                width: parent.width - rowThumb.width - parent.spacing
                                Text {
                                    width: parent.width
                                    text: watchedRow.name
                                    color: Theme.text
                                    font.pixelSize: Theme.fontBody
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: parent.width
                                    text: (watchedRow.label.length > 0 ? watchedRow.label + " · " : "")
                                        + Math.round(watchedRow.progressFraction * 100) + "%"
                                    color: Theme.textDim
                                    font.pixelSize: Theme.fontSmall
                                    elide: Text.ElideRight
                                }
                            }
                        }

                        // Fires immediately: it forgets one title, and watching
                        // it again puts it straight back.
                        AppButton {
                            id: forgetButton
                            ghost: true
                            iconGlyph: Icons.trash
                            tooltip: "Forget progress"
                            tone: "negative"
                            anchors.right: parent.right
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: progressController.forgetMedia(watchedRow.mediaId)
                        }
                    }
                }

                AppButton {
                    text: "Reset all progress"
                    tone: "negative"
                    // Gated on the total row count (watched included), not
                    // inProgressCount(): a user who finished every title they
                    // started has 0 in-progress rows but everything to reset.
                    // revision is read for the same reason as the Detail
                    // Forget button — totalCount() is a Slot, not a binding
                    // dependency, so without reading revision this would
                    // never re-evaluate when progress changes.
                    visible: progressController
                        && progressController.revision >= 0
                        && progressController.totalCount() > 0
                    onClicked: {
                        resetDialog.count = progressController.totalCount()
                        resetDialog.ask()
                    }
                }

                ConfirmDialog {
                    id: resetDialog
                    property int count: 0
                    heading: "Forget progress for " + count
                        + (count === 1 ? " saved position?" : " saved positions?")
                    body: "Every saved position is cleared, including finished ones. This cannot be undone."
                    confirmText: "Reset all"
                    onConfirmed: progressController.resetAll()
                }
            }

            SettingsCard {
                width: parent.width
                title: "About"
                enterDelay: 150

                Row {
                    spacing: 10
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Gravitas"
                        color: Theme.text
                        font.pixelSize: Theme.fontTitle
                        font.bold: true
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: versionLabel.implicitWidth + 16
                        height: 22
                        radius: 11
                        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
                        Text {
                            id: versionLabel
                            anchors.centerIn: parent
                            text: "v0.1.0"
                            color: Theme.accentHover
                            font.pixelSize: Theme.fontSmall
                        }
                    }
                }
                Text {
                    text: "A minimal, no-nonsense alternative to Stremio."
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }
                Text {
                    id: repoLink
                    text: "github.com/skyline69/gravitas"
                    color: linkHover.hovered ? Theme.accentHover : Theme.accent
                    font.pixelSize: Theme.fontSmall
                    font.underline: linkHover.hovered
                    HoverHandler { id: linkHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        // Through the controller, not Qt.openUrlExternally:
                        // the QML path spawns the browser under the frozen
                        // bundle's library path, where it dies silently.
                        onTapped: settingsController.openLink("https://github.com/skyline69/gravitas")
                    }
                }
            }
        }
    }
}
