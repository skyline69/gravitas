pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Gravitas
import "components"

Item {
    id: settings
    objectName: "settingsPage"
    signal back()

    // Fades itself in; StackView's transitions are empty so the stack stays
    // responsive during the animation (see PageFade).
    opacity: 0
    PageFade { id: pageFade; target: settings }
    StackView.onActivating: pageFade.restart()
    Component.onCompleted: {
        settings._arrange()
        pageFade.restart()
    }

    // One column on a narrow window, two side by side on a wide one. Not one
    // column stretched: a line of help text or an API-key field 1800px wide
    // is harder to read than one at 640. Two columns once each can be at
    // least 560px; each is capped at 640.
    readonly property int gutter: 20
    readonly property bool twoColumns: (page.width - 48 - gutter) / 2 >= 560
    readonly property real columnWidth: twoColumns
        ? Math.min(640, (page.width - 48 - gutter) / 2)
        : Math.min(page.width - 48, 720)
    onTwoColumnsChanged: settings._arrange()

    // The split is by topic and fixed, never balanced by height: balancing
    // would move a card to the other column when adding an addon or signing
    // in to Trakt changed a height, under the pointer of whoever did it.
    // Left: where content comes from and what is kept. Right: how playback
    // looks and sounds. Every card goes through the stash first, so each
    // column is rebuilt in declaration order whichever mode came before.
    function _arrange() {
        const cards = [addonsCard, metadataCard, integrationsCard, playerCard, languageCard,
                       subtitlesCard, sourcesCard, streamingCard, progressCard, aboutCard]
        const playback = [playerCard, languageCard, subtitlesCard, sourcesCard]
        for (const card of cards)
            card.parent = cardStash
        for (const card of cards)
            card.parent = settings.twoColumns && playback.includes(card) ? rightColumn : leftColumn
    }

    Flickable {
        id: page
        // Wheel scrolling that doesn't eat the click after it (see the component).
        WheelScroller { flick: page }
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
            width: settings.twoColumns ? settings.columnWidth * 2 + settings.gutter
                                       : settings.columnWidth
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

            Row {
                spacing: settings.gutter
                Column {
                    id: leftColumn
                    width: settings.columnWidth
                    spacing: 20
                }
                Column {
                    id: rightColumn
                    width: settings.columnWidth
                    spacing: 20
                    visible: settings.twoColumns
                }
            }

            // Where the cards are declared; _arrange() moves each into a
            // column as soon as the page exists.
            Item {
                id: cardStash
                visible: false

                SettingsCard {
                    id: addonsCard
                    width: parent.width
                    title: "Addons"
                    caption: "Stremio-compatible addon manifests"

                    Row {
                        width: parent.width
                        spacing: 8
                        // Manifest fetch + first catalog load take visible
                        // seconds; the row locks and a spinner slides in so the
                        // click reads as "working", not "ignored".
                        readonly property bool busy: AddonController && AddonController.installing
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
                                AddonController.addAddon(urlField.text)
                                urlField.text = ""
                            }
                        }
                    }

                    // Bootstrap is still restoring addons when Settings opens
                    // early; a spinner beats an empty card that pops full.
                    Row {
                        spacing: 10
                        visible: SettingsController && SettingsController.addonsLoading
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
                        id: addonList
                        // Wheel scrolling that doesn't eat the click after it (see the component).
                        WheelScroller { flick: addonList }
                        width: parent.width
                        height: Math.min(contentHeight, 320)
                        model: AddonListModel
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
                                onClicked: SettingsController.removeAddon(addonRow.addonId)
                            }
                        }
                    }
                }

                SettingsCard {
                    id: metadataCard
                    width: parent.width
                    title: "Metadata"
                    caption: "External metadata providers"
                    enterDelay: 60

                    Row {
                        width: parent.width
                        spacing: 8
                        // Bindings here guard the controller OBJECT, not just its
                        // property: the controllers can be gone before the engine at
                        // teardown, and every live binding re-evaluates on the way down, so a bare
                        // SettingsController.tmdbKey throws a TypeError on each exit.
                        AppTextField {
                            width: parent.width - savedTick.width - parent.spacing
                            placeholderText: "TMDB API key (optional, needed to open TVDB links)"
                            text: SettingsController ? SettingsController.tmdbKey : ""
                            onEditingFinished: {
                                SettingsController.setTmdbKey(text)
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
                            placeholderText: "MDBList API key (optional, enables Rotten Tomatoes + Letterboxd)"
                            text: SettingsController ? SettingsController.mdblistKey : ""
                            onEditingFinished: {
                                SettingsController.setMdblistKey(text)
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
                    id: integrationsCard
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
                                        if (!TraktController) return ""
                                        if (TraktController.authenticated)
                                            return "Connected"
                                                + (TraktController.username.length > 0
                                                    ? " as " + TraktController.username : "")
                                        if (TraktController.authInProgress)
                                            return "Approve in your browser. Code "
                                                + TraktController.userCode
                                        return "Sync what you watch to your Trakt profile"
                                    }
                                    color: TraktController && TraktController.authenticated
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
                                running: TraktController
                                    && (TraktController.authInProgress || TraktController.syncing)
                                visible: running
                            }
                            AppButton {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: TraktController && TraktController.authenticated
                                enabled: !(TraktController && TraktController.syncing)
                                text: TraktController && TraktController.syncing
                                    ? "Syncing…" : "Sync progress"
                                ghost: true
                                tooltip: "Pull unfinished playback from Trakt into Continue Watching"
                                onClicked: TraktController.syncNow()
                            }
                            AppButton {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: TraktController
                                    && !TraktController.authenticated
                                    && !TraktController.authInProgress
                                text: "Authenticate"
                                onClicked: TraktController.startAuth()
                            }
                            AppButton {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: TraktController && TraktController.authInProgress
                                text: "Cancel"
                                ghost: true
                                onClicked: TraktController.cancelAuth()
                            }
                            AppButton {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: TraktController && TraktController.authenticated
                                text: "Log out"
                                tone: "negative"
                                ghost: true
                                onClicked: TraktController.logout()
                            }
                        }
                    }

                    // Mirror policy. Off never blocks the local action — the
                    // sync always prefers local forgets/marks either way; these
                    // only decide whether Trakt is told about them.
                    Row {
                        visible: TraktController && TraktController.authenticated
                        spacing: 24
                        AppCheckBox {
                            checked: TraktController ? TraktController.syncForgets : true
                            label: "Sync forgets to Trakt"
                            tooltip: "Forgetting progress here also clears it on your Trakt account"
                            onToggled: (value) => TraktController.setSyncForgets(value)
                        }
                        AppCheckBox {
                            checked: TraktController ? TraktController.syncWatched : true
                            label: "Sync watched to Trakt"
                            tooltip: "Marking something watched here also adds it to your Trakt history"
                            onToggled: (value) => TraktController.setSyncWatched(value)
                        }
                    }
                }

                SettingsCard {
                    id: languageCard
                    width: parent.width
                    title: "Language"
                    caption: "Which audio and subtitles a video starts with"
                    enterDelay: 83

                    // Audio, then subtitles: a label and its menu per row. The
                    // options are {code, label} rows from SettingsController;
                    // code "" is "whatever the file marks as default".
                    Repeater {
                        model: [
                            { label: "Audio", audio: true },
                            { label: "Subtitles", audio: false }
                        ]
                        Row {
                            id: langRow
                            required property var modelData
                            width: parent.width
                            spacing: 12
                            Text {
                                width: 96
                                anchors.verticalCenter: parent.verticalCenter
                                text: langRow.modelData.label
                                color: Theme.textDim
                                font.pixelSize: Theme.fontSmall
                            }
                            AppComboBox {
                                id: langCombo
                                textRole: "label"
                                valueRole: "code"
                                iconSource: (row) => row.flag
                                    ? Qt.resolvedUrl("flags/" + row.flag + ".svg") : ""
                                model: !SettingsController ? []
                                    : langRow.modelData.audio ? SettingsController.audioLanguageOptions
                                    : SettingsController.subtitleLanguageOptions
                                // A binding, not an assignment: the popup sets the
                                // index from C++, which leaves it in place, so a
                                // change arriving from elsewhere still lands.
                                // indexOfValue() is a call, not a tracked read, so
                                // count is read to re-run it once the rows exist.
                                currentIndex: {
                                    const _rows = langCombo.count
                                    if (!SettingsController)
                                        return -1
                                    return langCombo.indexOfValue(langRow.modelData.audio
                                        ? SettingsController.audioLanguage
                                        : SettingsController.subtitleLanguage)
                                }
                                // The index, not currentValue: a pick from the
                                // search list arrives before currentIndex moves
                                // (see AppComboBox._choose).
                                onActivated: (index) => {
                                    if (langRow.modelData.audio)
                                        SettingsController.setAudioLanguage(langCombo.valueAt(index))
                                    else
                                        SettingsController.setSubtitleLanguage(langCombo.valueAt(index))
                                }
                            }
                        }
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "The player picks these tracks as it opens the file, so there is no "
                            + "switch and no wait. A video without the language plays its own "
                            + "default instead. Changes apply to the next thing you play; the "
                            + "player's track menu still switches the one that is playing."
                    }
                }

                SettingsCard {
                    id: subtitlesCard
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
                            id: previewBox
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 14
                            width: previewText.implicitWidth + 20
                            height: previewText.implicitHeight + 10
                            radius: 4
                            color: Qt.rgba(0, 0, 0,
                                (SettingsController && SettingsController.subBackOpacity !== undefined
                                    ? SettingsController.subBackOpacity : 0) / 100)

                            // Everything the copies below share with the real text.
                            readonly property string sample: "This is what subtitles will look like."
                            // Preview at ~45% of mpv's rendering scale so the
                            // strip stays proportionate.
                            readonly property real scale45: 0.45
                            readonly property int glyphSize: Math.round(
                                ((SettingsController && SettingsController.subFontSize) || 55) * scale45)
                            readonly property bool glyphBold:
                                SettingsController && SettingsController.subBold ? true : false
                            // mpv measures the border in the same units as the font,
                            // so the preview scales it the same way.
                            readonly property real outlinePx:
                                ((SettingsController && SettingsController.subBorderSize) || 0) * scale45

                            // The outline, drawn as black copies of the text ringed
                            // around it. Text.Outline was the obvious thing here, but
                            // it has ONE hardcoded width: every value above 0 drew
                            // the same hairline, so the slider looked like it did
                            // nothing -- or, next to a bolder-looking 0, like it ran
                            // backwards. This tracks the value the way mpv does.
                            // Sixteen copies: at the widest border the gaps between
                            // eight would scallop the thin strokes.
                            Repeater {
                                model: previewBox.outlinePx > 0 ? 16 : 0
                                Text {
                                    required property int index
                                    readonly property real angle: index * Math.PI / 8
                                    anchors.centerIn: parent
                                    anchors.horizontalCenterOffset: Math.cos(angle) * previewBox.outlinePx
                                    anchors.verticalCenterOffset: Math.sin(angle) * previewBox.outlinePx
                                    text: previewBox.sample
                                    color: "black"
                                    font.pixelSize: previewBox.glyphSize
                                    font.bold: previewBox.glyphBold
                                }
                            }
                            Text {
                                id: previewText
                                anchors.centerIn: parent
                                text: previewBox.sample
                                color: SettingsController && SettingsController.subColor
                                    ? SettingsController.subColor : "#FFFFFF"
                                font.pixelSize: previewBox.glyphSize
                                font.bold: previewBox.glyphBold
                            }
                        }
                    }

                    // External updates (reset) re-sync the sliders; user drags
                    // write through onMoved.
                    Connections {
                        target: SettingsController
                        function onSubtitleStyleChanged() {
                            if (!sizeSlider.pressed) sizeSlider.value = SettingsController.subFontSize
                            if (!outlineSlider.pressed) outlineSlider.value = SettingsController.subBorderSize
                            if (!backSlider.pressed) backSlider.value = SettingsController.subBackOpacity
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
                            value: SettingsController && SettingsController.subFontSize !== undefined
                                ? SettingsController.subFontSize : 55
                            onMoved: SettingsController.setSubFontSize(value)
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
                            value: SettingsController && SettingsController.subBorderSize !== undefined
                                ? SettingsController.subBorderSize : 3
                            onMoved: SettingsController.setSubBorderSize(value)
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
                            value: SettingsController && SettingsController.subBackOpacity !== undefined
                                ? SettingsController.subBackOpacity : 0
                            onMoved: SettingsController.setSubBackOpacity(value)
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
                                    id: swatch
                                    required property string modelData
                                    width: 26; height: 26; radius: 13
                                    color: swatch.modelData
                                    border.width: SettingsController.subColor === swatch.modelData ? 3 : 1
                                    border.color: SettingsController.subColor === swatch.modelData
                                        ? Theme.accent : Theme.borderStrong
                                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                                    TapHandler { onTapped: SettingsController.setSubColor(swatch.modelData) }
                                }
                            }
                        }
                        Item { width: 24; height: 1 }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Bold"
                            ghost: true
                            selected: SettingsController && SettingsController.subBold ? true : false
                            onClicked: SettingsController.setSubBold(!SettingsController.subBold)
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Reset"
                            ghost: true
                            onClicked: SettingsController.resetSubtitleStyle()
                        }
                    }
                }

                SettingsCard {
                    id: sourcesCard
                    width: parent.width
                    title: "Sources"
                    caption: "How the source list for a title is marked and ordered"
                    enterDelay: 105

                    AppCheckBox {
                        checked: SettingsController ? SettingsController.recommendSources : false
                        label: "Mark the best source per quality"
                        tooltip: "Puts one pick per resolution at the top, with the reason on hover"
                        onToggled: (value) => SettingsController.setRecommendSources(value)
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "For each picture your screen can actually show, one source is marked "
                            + "Recommended and lifted to the top. A pick has to fit your measured "
                            + "speed, decode without dropping frames here, and render correctly. "
                            + "With none of that known yet, nothing is marked."
                    }

                    Row {
                        spacing: 12
                        visible: SettingsController && SettingsController.strainedFormatCount > 0
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 380
                            wrapMode: Text.WordWrap
                            color: Theme.textDim
                            font.pixelSize: Theme.fontSmall
                            // Named, not counted: "4K AV1" is something the user
                            // can check against what they remember stuttering.
                            text: SettingsController
                                ? "This machine drops frames on " + SettingsController.strainedFormats
                                    + ". Those are not recommended."
                                : ""
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Forget"
                            ghost: true
                            onClicked: SettingsController.forgetDecodeVerdicts()
                        }
                    }

                    AppCheckBox {
                        checked: SettingsController ? SettingsController.sortByConnection : false
                        label: "Sort sources by my connection"
                        tooltip: "Puts the best quality your connection can actually stream at the top"
                        onToggled: (value) => SettingsController.setSortByConnection(value)
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        // Two states, and the difference matters to the user: with
                        // no measurement the list keeps the addon's own order, so
                        // saying so is the difference between "not working" and
                        // "nothing to work from yet".
                        text: SettingsController && SettingsController.connectionMbps > 0
                            ? "Measured: about " + SettingsController.connectionMbps
                                + " Mbps. Sources heavier than this are listed below the ones that fit."
                            : "Not measured yet. The first source list checks your speed once; "
                                + "after that, playback keeps the estimate current."
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        visible: SettingsController && SettingsController.screenHeight > 0
                        // Explains why a 4K source can sit below a 1440p one.
                        text: SettingsController
                            ? "Your screen renders video at up to " + SettingsController.screenHeight
                                + "p. Taller sources are downscaled to it, so they rank as "
                                + SettingsController.screenHeight + "p rather than by their label."
                            : ""
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "Speed is measured from your own playback and from the source host itself. "
                            + "No speed-test service is contacted."
                    }

                    AppCheckBox {
                        checked: SettingsController ? SettingsController.hideIncompatible : false
                        label: "Hide sources this machine can't display"
                        tooltip: "Dolby Vision profile 5 renders with wrong colours here"
                        onToggled: (value) => SettingsController.setHideIncompatible(value)
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "Dolby Vision profile 5 stores its colour in a form only one renderer "
                            + "understands, and it is not the one Gravitas can use, so those sources "
                            + "play magenta. A source list says how many it held back, and shows them "
                            + "on request."
                    }

                    Row {
                        spacing: 12
                        visible: SettingsController && SettingsController.knownBadSourceCount > 0
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.textDim
                            font.pixelSize: Theme.fontSmall
                            text: SettingsController
                                ? SettingsController.knownBadSourceCount
                                    + (SettingsController.knownBadSourceCount === 1
                                        ? " source is remembered as unplayable"
                                        : " sources are remembered as unplayable")
                                : ""
                        }
                        AppButton {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Forget"
                            ghost: true
                            onClicked: SettingsController.forgetBadSources()
                        }
                    }
                }

                SettingsCard {
                    id: playerCard
                    width: parent.width
                    title: "Player"
                    caption: "What plays the video"
                    enterDelay: 80

                    Row {
                        width: parent.width
                        spacing: 12
                        Text {
                            width: 96
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Video player"
                            color: Theme.textDim
                            font.pixelSize: Theme.fontSmall
                        }
                        AppComboBox {
                            id: playerCombo
                            textRole: "label"
                            valueRole: "code"
                            model: SettingsController ? SettingsController.videoPlayerOptions : []
                            // A binding, as the language menus do (see there).
                            currentIndex: {
                                const _rows = playerCombo.count
                                return SettingsController
                                    ? playerCombo.indexOfValue(SettingsController.videoPlayer)
                                    : -1
                            }
                            onActivated: (index) => SettingsController.setVideoPlayer(playerCombo.valueAt(index))
                        }
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "Gravitas' own player renders HDR and Dolby Vision correctly, decodes on "
                            + "the graphics card and switches audio tracks without a pause. mpv is the "
                            + "long-standing alternative, if something plays better there."
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.accent
                        font.pixelSize: Theme.fontSmall
                        visible: text !== ""
                        text: !SettingsController ? ""
                            : SettingsController.videoPlayer === "native" && !SettingsController.nativePlayerAvailable
                                ? "Gravitas' own player isn't included in this copy, so videos play through mpv."
                            : SettingsController.videoPlayer !== SettingsController.videoPlayerRunning
                                ? "Restart Gravitas to switch players."
                            : ""
                    }
                }

                SettingsCard {
                    id: streamingCard
                    width: parent.width
                    title: "Streaming"
                    caption: "How a source is fetched once you press play"
                    enterDelay: 108

                    AppCheckBox {
                        checked: SettingsController ? SettingsController.parallelStreaming : false
                        label: "Fetch streams over several connections"
                        tooltip: "Starts playback sooner, and works around hosts that cap a connection"
                        onToggled: (value) => SettingsController.setParallelStreaming(value)
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "The player itself reads one connection, front to back, so a host that "
                            + "limits what one connection may do limits playback no matter how fast "
                            + "your line is. Gravitas can fetch the file in blocks over four "
                            + "connections at once and feed the player locally, which also keeps what "
                            + "it fetched so seeking back costs nothing."
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "It also starts playback sooner: opening a file takes several "
                            + "requests, and kept connections answer them faster than new ones. If "
                            + "a host is not reachable this way, the player is handed the stream "
                            + "directly after a short pause. Changing this applies to the next thing "
                            + "you play."
                    }

                    AppCheckBox {
                        checked: SettingsController ? SettingsController.onlineSegments : false
                        label: "Look up intro and credits times online"
                        tooltip: "Asks SkipDB when a video does not mark them itself"
                        onToggled: (value) => SettingsController.setOnlineSegments(value)
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.textDim
                        font.pixelSize: Theme.fontSmall
                        text: "Skip intro and the next-episode prompt come from the video's own "
                            + "chapters when it has them. When it does not, this asks SkipDB, a "
                            + "community database, once per episode (the answer is kept), which "
                            + "tells it which episode you are watching."
                    }
                }

                SettingsCard {
                    id: progressCard
                    width: parent.width
                    title: "Watch progress"
                    caption: "Saved playback positions, kept on this device only"
                    enterDelay: 120

                    // The list is only correct while the page is open; repopulate
                    // on entry rather than keeping it live for a hidden page.
                    Component.onCompleted: ProgressController.refreshWatched()

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
                        // Wheel scrolling that doesn't eat the click after it (see the component).
                        WheelScroller { flick: watchedList }
                        width: parent.width
                        height: Math.min(contentHeight, 320)
                        visible: count > 0
                        model: WatchedListModel
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
                                onClicked: ProgressController.forgetMedia(watchedRow.mediaId)
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
                        visible: ProgressController
                            && ProgressController.revision >= 0
                            && ProgressController.totalCount() > 0
                        onClicked: {
                            resetDialog.count = ProgressController.totalCount()
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
                        onConfirmed: ProgressController.resetAll()
                    }
                }

                SettingsCard {
                    id: aboutCard
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
                            onTapped: SettingsController.openLink("https://github.com/skyline69/gravitas")
                        }
                    }
                }
            }
        }
    }
}
