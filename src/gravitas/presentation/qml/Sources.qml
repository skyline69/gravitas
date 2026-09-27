pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Gravitas
import "components"

// Stream picker for one episode, pushed from the series Detail page.
Item {
    id: sources
    objectName: "sourcesPage"

    // Fades itself in; StackView's transitions are empty so the stack stays
    // responsive during the animation (see PageFade).
    opacity: 0
    PageFade { id: pageFade; target: sources }
    StackView.onActivating: pageFade.restart()
    Component.onCompleted: pageFade.restart()
    signal playUrl(string url, var headers)
    signal back()

    // The row clicked while the list was the stored one, waiting for its
    // fresh link; -1 when none is.
    property int pendingRow: -1

    // Plays `row` of the list on screen: its identity, its label, the rest
    // of the list to fall back to, then the link.
    function playRow(row) {
        const source = DetailController.sourceAt(row)
        if (!source.url)
            return
        // Identity must land before play(); the controller reads it to
        // resume and to record.
        PlayerController.setMediaContext(DetailController.mediaContext())
        // Which row this is: mpv reports the DV profile, but only the list
        // knows the release it belongs to.
        PlayerController.setSourceLabel(source.name, source.title)
        PlayerController.setSourceFile(source.filename)
        // What to fall back to if this source's host refuses the connection,
        // which takes every source on that CDN node with it and says nothing
        // about the title.
        PlayerController.setSourceQueue(DetailController.sourceQueue(row))
        sources.playUrl(source.url, source.headers)
    }

    Connections {
        target: DetailController
        function onFreshSourceReady(row: int) {
            if (sources.pendingRow < 0)
                return
            sources.pendingRow = -1
            if (row >= 0)
                sources.playRow(row)
        }
    }

    Rectangle { anchors.fill: parent; color: Theme.bg }

    Column {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 24
        spacing: 16

        Row {
            spacing: 12
            BackButton {
                anchors.verticalCenter: parent.verticalCenter
                onClicked: sources.back()
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: DetailController ? DetailController.sourcesLabel : "Sources"
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.bold: true
                elide: Text.ElideRight
            }
            AppSpinner {
                anchors.verticalCenter: parent.verticalCenter
                width: 18; height: 18
                running: DetailController ? DetailController.streamsLoading : false
            }
        }

        SourcesEmptyState {
            count: list.count
            loading: DetailController !== null && DetailController.streamsLoading
            failed: DetailController !== null && DetailController.sourcesFailed
            onRetry: DetailController.retrySources()
        }

        // What the compatibility filter held back, and the way past it. The
        // count is shown rather than silently applied: a filter the user
        // cannot see is one they cannot correct.
        Row {
            spacing: 8
            visible: DetailController && DetailController.hiddenSourceCount > 0
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: DetailController
                    ? DetailController.hiddenSourceCount
                        + (DetailController.hiddenSourceCount === 1
                            ? " source hidden: this machine can't display it correctly"
                            : " sources hidden: this machine can't display them correctly")
                    : ""
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
            AppButton {
                anchors.verticalCenter: parent.verticalCenter
                text: "Show anyway"
                ghost: true
                onClicked: DetailController.showHiddenSources()
            }
        }
    }

    // Skeleton rows while the resolve is in flight and nothing has arrived —
    // the page reads as "loading sources", not empty-then-sudden-list.
    Column {
        anchors.top: header.bottom
        anchors.topMargin: 16
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        spacing: 8
        visible: list.count === 0
            && DetailController && DetailController.streamsLoading
        Repeater {
            model: 6
            StreamRowSkeleton {
                required property int index
                width: parent.width
                pulseDelay: index * 90
            }
        }
    }

    ListView {
        id: list
        // Wheel scrolling that doesn't eat the click after it (see the component).
        WheelScroller { flick: list }
        anchors.top: header.bottom
        anchors.topMargin: 16
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.bottomMargin: 24
        spacing: 8
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        // Rows dissolve into the page at the top and bottom instead of ending
        // in the clip (see EdgeFade). The bar sits outside the view, in
        // the right gutter, so the feather does not take it too.
        // Not on the software renderer: it has no shaders, and the effect
        // would draw nothing at all where the view should be.
        layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
        layer.effect: EdgeFade { view: list }
        ScrollBar.vertical: AppScrollBar {
            parent: list.parent
            anchors.top: list.top
            anchors.bottom: list.bottom
            anchors.left: list.right
        }
        model: StreamModel
        // Rows fly in when the list fills from empty -- page open, first answer,
        // a new episode -- and not when scrolling builds more of them later. Armed
        // while empty; disarmed shortly after the first rows exist, which covers
        // every delegate built in that fill whatever order count and creation run in.
        property bool flyInArmed: true
        onCountChanged: {
            if (list.count === 0)
                list.flyInArmed = true
            else if (list.flyInArmed)
                flyInDisarm.restart()
        }
        Timer { id: flyInDisarm; interval: 300; onTriggered: list.flyInArmed = false }
        // The model leaves every section empty when nothing is recommended, and
        // the delegate collapses to nothing on an empty string — so a list with
        // no marks has no headings rather than one that says "All sources"
        // over everything.
        section.property: "section"
        section.criteria: ViewSection.FullString
        section.delegate: Item {
            id: heading
            required property string section
            width: list.width
            height: heading.section.length > 0 ? sectionLabel.implicitHeight + 18 : 0
            Text {
                id: sectionLabel
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 6
                visible: heading.section.length > 0
                text: heading.section === "Recommended"
                    ? "Recommended: best for this screen and connection"
                    : heading.section
                color: heading.section === "Recommended" ? Theme.accentHover : Theme.textDim
                font.pixelSize: Theme.fontSmall
                font.bold: heading.section === "Recommended"
            }
        }
        delegate: StreamRow {
            required property var model
            required property int index
            width: list.width
            name: model.name
            subtitle: model.subtitle
            resolution: model.resolution
            instant: model.instant
            tags: model.tags
            stars: model.stars
            detailText: model.extra
            overBudget: model.overBudget
            oversized: model.oversized
            recommended: model.recommended
            reason: model.reason
            pending: sources.pendingRow === index
            // Staggered by position; capped so a long list's last visible
            // row does not wait on the ones above it for long.
            Component.onCompleted: if (list.flyInArmed) flyIn(Math.min(index, 10) * 45)
            onClicked: {
                if (model.external) {
                    PlayerController.openExternal(model.external)
                    return
                }
                if (!model.url)
                    return
                // A stored list's links may be from another session (bound
                // to an address this machine no longer has, or expired):
                // wait for the fresh list's link to the same release.
                if (DetailController.sourcesStored) {
                    sources.pendingRow = index
                    DetailController.pickWhenFresh(index)
                    return
                }
                sources.playRow(index)
            }
        }
    }
}
