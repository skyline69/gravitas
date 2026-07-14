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
                    AppTextField {
                        id: urlField
                        width: parent.width - addButton.width - parent.spacing
                        placeholderText: "Addon manifest URL…"
                        onAccepted: addButton.clicked()
                    }
                    AppButton {
                        id: addButton
                        text: "Add"
                        tone: "positive"
                        onClicked: {
                            if (urlField.text.trim().length === 0)
                                return
                            addonController.addAddon(urlField.text)
                            urlField.text = ""
                        }
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
                    add: Transition {
                        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durMed }
                    }
                    displaced: Transition {
                        NumberAnimation { property: "y"; duration: Theme.durMed; easing.type: Easing.OutCubic }
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
                    AppTextField {
                        width: parent.width - savedTick.width - parent.spacing
                        placeholderText: "TMDB API key (optional — needed to open TVDB links)"
                        text: settingsController.tmdbKey
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
                            (settingsController.subBackOpacity !== undefined
                                ? settingsController.subBackOpacity : 0) / 100)
                        Text {
                            id: previewText
                            anchors.centerIn: parent
                            text: "This is what subtitles will look like."
                            color: settingsController.subColor ? settingsController.subColor : "#FFFFFF"
                            // Preview at ~45% of mpv's rendering scale so the
                            // strip stays proportionate.
                            font.pixelSize: Math.round((settingsController.subFontSize || 55) * 0.45)
                            font.bold: settingsController.subBold ? true : false
                            style: settingsController.subBorderSize > 0 ? Text.Outline : Text.Normal
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
                        value: settingsController.subFontSize !== undefined ? settingsController.subFontSize : 55
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
                        value: settingsController.subBorderSize !== undefined ? settingsController.subBorderSize : 3
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
                        value: settingsController.subBackOpacity !== undefined ? settingsController.subBackOpacity : 0
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
                                border.width: settingsController.subColor === modelData ? 3 : 1
                                border.color: settingsController.subColor === modelData
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
                        selected: settingsController.subBold ? true : false
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
                title: "About"
                enterDelay: 120

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
                    text: "A memory-efficient, Linux-first media center."
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
                        onTapped: Qt.openUrlExternally("https://github.com/skyline69/gravitas")
                    }
                }
            }
        }
    }
}
