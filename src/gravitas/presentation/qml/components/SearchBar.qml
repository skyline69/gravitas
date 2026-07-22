import QtQuick
import QtQuick.Controls
import QtQuick.Controls as QQC
import "."

Item {
    id: bar
    signal openDetail(string type, string id)
    signal openResults()

    // True while the input holds keyboard focus (drives the width + dropdown).
    readonly property alias searchActive: field.activeFocus

    // Drop keyboard focus. Moving active focus to the (non-text) root Item is
    // what actually blurs a TextField — setting `field.focus = false` alone
    // does not reliably release active focus.
    function unfocus() { bar.forceActiveFocus() }

    // Clear the query and blur. Defined on the root so `field` resolves — the
    // dropdown delegate is in the Popup's own scope and can't see `field`.
    function reset() {
        field.text = ""
        bar.unfocus()
    }

    // Whether a search is in flight (drives the inline field spinner and the
    // skeleton placeholder rows).
    property bool loading: false
    Connections {
        target: searchController
        function onLoadingChanged(l) { bar.loading = l }
    }

    implicitHeight: 36
    // Width (and its focus-expand animation) is driven by the parent TopBar so
    // it can stay responsive — shrinking to fit the gap between the tabs and
    // the gear on narrow windows.

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.surfacePress
        // Accent border while the input is focused; fades in/out (constant
        // width keeps the layout from shifting).
        border.width: 2
        border.color: field.activeFocus ? Theme.accent : "transparent"
        Behavior on border.color { ColorAnimation { duration: Theme.durMed } }
        AppIcon {
            id: mag
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacing
            anchors.verticalCenter: parent.verticalCenter
            glyph: Icons.search
            font.pixelSize: Theme.fontBody
            color: Theme.textDim
        }
        // Small spinner inside the field's right edge while loading — so
        // "loading" reads as activity in the input, not a spinner shoved above
        // the results.
        AppSpinner {
            id: fieldBusy
            width: 16
            height: 16
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacing
            anchors.verticalCenter: parent.verticalCenter
            running: bar.loading
        }
        TextField {
            id: field
            anchors.left: mag.right
            anchors.leftMargin: Theme.spacing / 2
            anchors.right: fieldBusy.running ? fieldBusy.left : parent.right
            anchors.rightMargin: Theme.spacing
            anchors.verticalCenter: parent.verticalCenter
            placeholderText: "Search or paste an IMDB/TVDB link…"
            placeholderTextColor: Theme.textDim
            color: Theme.text
            font.pixelSize: Theme.fontBody
            selectionColor: Theme.accent
            selectedTextColor: Theme.text
            background: null
            onTextChanged: searchController.queueSearch(text)
            onAccepted: if (text.length > 0) {
                searchController.submit(text) // fresh search for the exact query
                bar.openResults()
                bar.unfocus() // blur so the live dropdown hides
            }
            Keys.onEscapePressed: { text = ""; field.focus = false }

            // Qt's own editing menu is native on Windows and ignores the
            // theme; ours replaces it. Qualified because `ContextMenu` also
            // names a component of ours in this directory.
            QQC.ContextMenu.menu: null
            TextEditMenu { editor: field }
        }
    }

    // Live-preview dropdown.
    Popup {
        id: dropdown
        y: bar.height + 8
        width: bar.width
        padding: 4
        visible: field.activeFocus && (list.count > 0 || bar.loading)
        closePolicy: Popup.NoAutoClose

        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surface
            border.width: 1
            border.color: Theme.borderStrong
        }

        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "y"; from: bar.height + 2; to: bar.height + 8; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
        }

        contentItem: Column {
            spacing: 0

            // Skeleton placeholder rows: shown only on a FRESH query (loading
            // with nothing yet). Once streamed results arrive, list.count > 0
            // and the real rows take over.
            Repeater {
                model: (bar.loading && list.count === 0) ? 6 : 0
                delegate: Item {
                    width: list.width
                    height: 64
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        running: bar.loading
                        NumberAnimation { from: 0.45; to: 1.0; duration: 700; easing.type: Easing.InOutQuad }
                        NumberAnimation { from: 1.0; to: 0.45; duration: 700; easing.type: Easing.InOutQuad }
                    }
                    Row {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 10
                        Rectangle { width: 36; height: 52; radius: Theme.radiusSmall; color: Theme.surfaceHover }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 8
                            Rectangle { width: 170; height: 12; radius: 4; color: Theme.surfaceHover }
                            Rectangle { width: 90; height: 10; radius: 4; color: Theme.surfaceHover }
                        }
                    }
                }
            }

            ListView {
                id: list
                // Wheel scrolling that doesn't eat the click after it (see the component).
                WheelScroller { flick: list }
                width: parent.width
                height: Math.min(contentHeight, 360)
                model: searchResultsModel
                interactive: true
                clip: true
                ScrollBar.vertical: AppScrollBar {}
                delegate: Rectangle {
                    id: resultRow
                    width: ListView.view.width
                    height: 64
                    radius: Theme.radiusSmall
                    required property string mediaId
                    required property string type
                    required property string name
                    required property string poster
                    required property string year
                    // Instant highlight — a fade here trails behind the cursor
                    // when sweeping across rows and reads as flicker.
                    color: rowHover.hovered ? Theme.surfaceHover : "transparent"
                    Row {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 10
                        Item {
                            width: 36
                            height: 52
                            // Filler for missing/broken thumbnails.
                            Rectangle {
                                anchors.fill: parent
                                radius: Theme.radiusSmall
                                color: Theme.surfaceHover
                                visible: !resultRow.poster || thumb.status === Image.Error
                                AppIcon {
                                    anchors.centerIn: parent
                                    glyph: resultRow.type === "series" ? Icons.liveTv : Icons.theaters
                                    font.pixelSize: 18
                                    color: Theme.textDim
                                }
                            }
                            Image {
                                id: thumb
                                anchors.fill: parent
                                fillMode: Image.PreserveAspectCrop
                                // Tiny thumbnail — request a small variant, not
                                // the full poster.
                                source: Img.sized(resultRow.poster, 90)
                                sourceSize.width: 90
                                asynchronous: true
                                cache: true
                            }
                        }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                text: resultRow.name
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                                elide: Text.ElideRight
                                width: list.width - 70
                            }
                            Text {
                                text: resultRow.year
                                color: Theme.textDim
                                font.pixelSize: Theme.fontSmall
                                visible: text.length > 0
                            }
                        }
                    }
                    HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        // Suggestion rows hang over the page below the bar;
                        // the exclusive grab keeps the pick from also
                        // reaching the content behind the dropdown.
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: {
                            bar.openDetail(resultRow.type, resultRow.mediaId)
                            bar.reset()
                        }
                    }
                }
            }
        }
    }
}
