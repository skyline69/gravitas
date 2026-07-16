import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Qt5Compat.GraphicalEffects
import "components"

Item {
    id: root
    signal openDetail(string type, string id)
    signal back()

    // Mirrors DiscoverController's loading signal (see AppSpinner pattern in
    // the old page); drives the count shimmer + first-load spinner.
    property bool loading: false
    Connections {
        target: discoverController
        function onLoadingChanged(l) { root.loading = l }
    }

    readonly property var sortKeys: ["default", "name", "year", "rating"]

    // True when any local control deviates from its default; shows Reset.
    readonly property bool filtersDirty: (discoverController && discoverController.genreIndex > 0)
        || filterField.text.length > 0
        || sortBox.currentIndex > 0

    function resetFilters() {
        filterField.text = "" // onTextChanged clears the proxy filter
        sortBox.currentIndex = 0
        discoverProxy.setSortKey("default")
        if (discoverController && discoverController.genreIndex > 0)
            discoverController.selectGenre(0)
    }

    // Floating pill matching TopBar's language: content scrolls underneath.
    Rectangle {
        id: bar
        z: 1
        anchors.top: parent.top
        anchors.topMargin: 12
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(parent.width - 24, 1040)
        height: 56
        color: Theme.surface
        radius: Theme.radius * 2
        border.width: 1
        border.color: Theme.borderStrong
        // Soft shadow sells the float once posters slide underneath.
        layer.enabled: true
        layer.effect: DropShadow {
            transparentBorder: true
            radius: 24
            samples: 25
            verticalOffset: 4
            color: "#66000000"
        }

        RowLayout {
            id: controls
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 16
            anchors.rightMargin: 16
            anchors.verticalCenter: parent.verticalCenter
            spacing: Theme.spacing

            AppButton {
                ghost: true
                iconGlyph: Icons.arrowLeft
                tooltip: "Back"
                onClicked: root.back()
            }

            SegmentedControl {
                model: discoverController ? discoverController.typeOptions : []
                currentIndex: discoverController ? discoverController.typeIndex : 0
                onActivated: (index) => { if (discoverController) discoverController.selectType(index) }
            }

            AppComboBox {
                sizeCap: 300
                Layout.fillWidth: true
                Layout.minimumWidth: 110
                Layout.maximumWidth: implicitWidth
                model: discoverController ? discoverController.catalogOptions : []
                currentIndex: discoverController ? discoverController.catalogIndex : 0
                onActivated: (index) => { if (discoverController) discoverController.selectCatalog(index) }
            }

            AppComboBox {
                sizeCap: 220
                Layout.fillWidth: true
                Layout.minimumWidth: 100
                Layout.maximumWidth: implicitWidth
                model: discoverController ? discoverController.genreOptions : []
                currentIndex: discoverController ? discoverController.genreIndex : 0
                onActivated: (index) => { if (discoverController) discoverController.selectGenre(index) }
            }

            // Compact local filter: narrows the loaded grid by title. Soaks up
            // the bar's slack and expands its cap while focused.
            Rectangle {
                id: filterBox
                Layout.fillWidth: true
                Layout.minimumWidth: 110
                Layout.maximumWidth: filterField.activeFocus ? 240 : 170
                Layout.preferredHeight: Theme.controlHeight
                radius: Theme.radius
                color: Theme.surfacePress
                border.width: 2
                border.color: filterField.activeFocus ? Theme.accent : "transparent"
                Behavior on Layout.maximumWidth { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
                Behavior on border.color { ColorAnimation { duration: Theme.durMed } }

                AppIcon {
                    id: filterMag
                    anchors.left: parent.left
                    anchors.leftMargin: Theme.spacing
                    anchors.verticalCenter: parent.verticalCenter
                    glyph: Icons.search
                    font.pixelSize: Theme.fontBody
                    color: Theme.textDim
                }
                TextField {
                    id: filterField
                    objectName: "filterField"
                    anchors.left: filterMag.right
                    anchors.leftMargin: Theme.spacing / 2
                    anchors.right: parent.right
                    anchors.rightMargin: Theme.spacing
                    anchors.verticalCenter: parent.verticalCenter
                    placeholderText: "Filter…"
                    placeholderTextColor: Theme.textDim
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    selectionColor: Theme.accent
                    selectedTextColor: Theme.text
                    background: null
                    onTextChanged: discoverProxy.setFilterText(text)
                    // Blur by moving focus to the (non-text) page root — same
                    // trick as SearchBar; clearing `focus` alone doesn't
                    // release active focus.
                    Keys.onEscapePressed: { text = ""; root.forceActiveFocus() }
                }
            }

            AppComboBox {
                id: sortBox
                model: ["Default", "Name", "Newest", "Rating"]
                onActivated: (index) => discoverProxy.setSortKey(root.sortKeys[index])
            }

            AppButton {
                ghost: true
                iconGlyph: Icons.x
                tooltip: "Reset filters"
                visible: opacity > 0
                opacity: root.filtersDirty ? 1 : 0
                scale: root.filtersDirty ? 1 : 0.6
                Behavior on opacity { NumberAnimation { duration: Theme.durFast } }
                Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutBack } }
                onClicked: root.resetFilters()
            }

            // Uncapped spacer: absorbs whatever slack is left once the combos
            // and the filter hit their caps, pinning the count to the right.
            Item { Layout.fillWidth: true }

            // Result count ("12 / 128" while narrowed); swaps to a shimmer
            // bar while a page is loading. First thing to go when the bar
            // runs out of width.
            Text {
                id: countLabel
                visible: !root.loading && bar.width >= 900
                text: discoverProxy.count === discoverProxy.totalCount
                    ? discoverProxy.count + " titles"
                    : discoverProxy.count + " / " + discoverProxy.totalCount
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
                onTextChanged: countFade.restart()
                NumberAnimation {
                    id: countFade
                    target: countLabel
                    property: "opacity"
                    from: 0.3; to: 1.0
                    duration: Theme.durMed
                }
            }
            Rectangle {
                visible: root.loading && bar.width >= 900
                Layout.preferredWidth: 70
                Layout.preferredHeight: 12
                radius: 4
                color: Theme.surfaceHover
                SequentialAnimation on opacity {
                    loops: Animation.Infinite
                    running: root.loading
                    NumberAnimation { from: 0.45; to: 1.0; duration: 700; easing.type: Easing.InOutQuad }
                    NumberAnimation { from: 1.0; to: 0.45; duration: 700; easing.type: Easing.InOutQuad }
                }
            }
        }
    }

    GridView {
        id: grid
        maximumFlickVelocity: 12000
        flickDeceleration: 8000
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.bottomMargin: 24
        // Content inset (not an anchor margin): the first row rests below the
        // floating bar, but scrolled posters slide underneath it.
        topMargin: bar.y + bar.height + 12
        // Distribute the leftover width among the columns instead of leaving
        // a dead strip on the right: as many 180px columns as fit, each cell
        // widened to an equal share (the card centres itself in the cell).
        cellWidth: Math.floor(width / Math.max(1, Math.floor(width / 180)))
        cellHeight: 300
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        model: discoverProxy
        // Filtering reads as movement, not a repaint: entering items fade in
        // with a slight rise, survivors glide to their new slots.
        add: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "y"; from: 12; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        // Initial page load and filter-driven model resets fade in the same
        // way (`add` doesn't run on a model reset).
        populate: Transition {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.durMed * 2; easing.type: Easing.OutCubic }
            NumberAnimation { property: "y"; from: 16; duration: Theme.durMed * 2; easing.type: Easing.OutCubic }
        }
        displaced: Transition {
            NumberAnimation { properties: "x,y"; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        delegate: PosterCard {
            // fill the whole cell and centre the poster inside it, so the
            // hover scale-up grows into the cell's slack instead of past the
            // grid's clip edge (fixes edge-column/row clipping)
            width: grid.cellWidth
            height: 300
            title: model.name
            posterUrl: model.poster ? model.poster : ""
            mediaType: model.type
            progressFraction: model.progressFraction
            watched: model.watched
            forgetContext: ({
                mediaId: model.id,
                videoId: "",
                type: model.type,
                name: model.name,
                poster: model.poster ? model.poster : "",
                label: ""
            })
            onClicked: root.openDetail(model.type, model.id)
        }
        onAtYEndChanged: if (atYEnd && discoverController) discoverController.loadMore()
    }

    Item {
        anchors.fill: grid
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: (w) => Scroll.wheel(grid, w)
        }
    }

    // Centered spinner only for the initial empty load; later loads shimmer
    // in the bar instead.
    AppSpinner {
        anchors.centerIn: parent
        running: root.loading && discoverProxy.count === 0
    }
}
