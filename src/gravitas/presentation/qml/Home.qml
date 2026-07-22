import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    objectName: "homePage"

    // Fades itself in; StackView's transitions are empty so the stack stays
    // responsive during the animation (see PageFade).
    opacity: 0
    PageFade { id: pageFade; target: home }
    StackView.onActivating: pageFade.restart()
    Component.onCompleted: pageFade.restart()
    signal openDetail(string type, string id)
    signal seeAll(string addonId, string type, string catalogId)
    // Set by Main from the TopBar tab. "watchlist" swaps the catalog rows for
    // the watchlist grid; anything else shows the (already filtered) rows.
    property string catalogMode: "all"
    readonly property bool watchlistMode: catalogMode === "watchlist"

    // One reveal, not a page assembling itself: rows stay hidden behind the
    // boot spinner until the whole first load (catalog + Continue Watching +
    // Trakt) has landed, then fade in together.
    readonly property bool booting: catalogController && catalogController.booting

    // Ease back to the top on every tab switch. Filter changes are surgical
    // now (no model reset), so the view would otherwise keep the previous
    // tab's scroll offset — disorienting mid-list. Any user scroll (drag or
    // wheel) cancels the glide immediately; fighting the user is worse than
    // landing short.
    onCatalogModeChanged: watchlistMode ? watchlistToTop.restart() : rowsToTop.restart()
    NumberAnimation {
        id: rowsToTop
        target: rowsView
        property: "contentY"
        to: rowsView.originY - rowsView.topMargin
        duration: 480
        easing.type: Easing.OutCubic
    }
    NumberAnimation {
        id: watchlistToTop
        target: watchlistFlick
        property: "contentY"
        to: watchlistFlick.originY - watchlistFlick.topMargin
        duration: 480
        easing.type: Easing.OutCubic
    }

    ListView {
        id: rowsView
        // Wheel scrolling that doesn't eat the click after it (see the component).
        WheelScroller { flick: rowsView }
        // Stays in the scene (visible, opacity 0) through boot so delegates
        // incubate and posters decode BEHIND the spinner — hiding it with
        // `visible: false` would defer all of that to the reveal frame,
        // which is exactly the burst that made the spinner's last moments
        // stutter. enabled gates clicks on the still-invisible cards.
        visible: !home.watchlistMode
        enabled: !home.booting
        opacity: home.booting ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: Theme.durMed * 2; easing.type: Easing.OutCubic } }
        maximumFlickVelocity: 12000
        flickDeceleration: 8000
        anchors.fill: parent
        anchors.leftMargin: 24
        // Reach the window edge so the scrollbar hugs it; delegates keep a
        // right inset (see delegate width) so content isn't under the bar.
        anchors.rightMargin: 0
        anchors.bottomMargin: 24
        // Reserve space for the floating bar (top margin 12 + height 56 + gap);
        // rows still scroll up underneath it and are hidden by the opaque bar.
        topMargin: 80
        spacing: 28
        clip: true
        // Pre-build rows below the fold so their posters are fetched and
        // decoded before the user scrolls to them — scrolling should reveal
        // finished cards, not loading spinners. Delegate creation is spread
        // over frames by the view, so this does not stall the reveal.
        //
        // Ten rows' worth, not the sixteen this used to hold: each row keeps
        // a strip of decoded posters alive, and on a Retina panel every one
        // of those textures is four times the pixels. Measured on Linux at
        // 1x, trimming it took the process from 272 MB to 244 MB with the
        // same rows on screen; a 2x display pays that difference several
        // times over, in texture uploads as well as memory.
        cacheBuffer: 3600
        // Recycle row delegates that do scroll out instead of destroying and
        // re-instantiating them — creation cost is the other half of scroll
        // stutter.
        reuseItems: true
        ScrollBar.vertical: AppScrollBar {}
        onMovementStarted: rowsToTop.stop()
        model: catalogRowsModel
        // Each row loads through an ASYNC Loader: when a model change lands,
        // the view would otherwise create every visible row's strip — and
        // each strip its visible cards — in one synchronous chunk on the GUI
        // thread, stalling the render sync (the boot spinner hitched on
        // every such burst). The incubator spreads that creation over
        // frames instead. The Loader carries the strip's geometry (header +
        // gap + cards, all from Theme) so row layout is stable before its
        // content exists.
        delegate: Loader {
            id: rowLoader
            width: rowsView.width - 24
            // Mirrors CatalogRowStrip.implicitHeight, which cannot be read
            // until the async load finishes — a subtitled row's strip is
            // taller, so it must be reserved that height from the start or
            // the strip's clip cuts the cards it can't fit.
            // Read defensively: a pooled row still evaluates its bindings
            // while the view has taken its model row away.
            readonly property bool rowContinueWatching:
                typeof model !== "undefined" && model.continueWatching === true
            height: Theme.posterRowHeaderHeight
                + (rowContinueWatching ? Theme.posterStripSubtitleHeight
                                       : Theme.posterStripHeight)
            asynchronous: true
            // Same recycled-delegate hygiene as PosterCard, and derived the
            // same way: the view sets index to -1 while a row sits in the
            // pool. Tab switches are what pool rows here, and they arrive as
            // fast as the user can click, so this must not depend on two
            // imperative writes landing in the right order — a row left
            // hidden while attached is a blank home page.
            // NOT a `required property int index`: declaring one switches the
            // delegate to required-properties mode, and `model` stops being
            // injected — every model.title below turns into a ReferenceError.
            readonly property int rowIndex: typeof index !== "undefined" ? index : 0
            visible: rowIndex >= 0
            sourceComponent: CatalogRowStrip {
                width: rowsView.width - 24
                // The page view's visible bounds in this row's coordinates,
                // so cards feather into the window's top/bottom edges
                // instead of being sliced by the view's clip.
                viewClipTop: rowsView.contentY - rowLoader.y
                viewClipBottom: rowsView.contentY + rowsView.height - rowLoader.y
                title: model.title
                addonId: model.addonId
                type: model.type
                catalogId: model.catalogId
                posters: model.posters
                continueWatching: model.continueWatching
                onOpenDetail: (t, id) => home.openDetail(t, id)
                onSeeAll: (aid, t, cid) => home.seeAll(aid, t, cid)
            }
        }
    }

    // Faster mouse-wheel scrolling. A plain Item + WheelHandler overlay (not a
    // MouseArea, so it doesn't hijack the cursor/hover of the posters below);
    // sitting above the view, it receives the wheel where a WheelHandler inside
    // the Flickable would not.
    Item {
        anchors.fill: rowsView
        visible: rowsView.visible
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: (w) => {
                rowsToTop.stop()
                Scroll.wheel(rowsView, w)
            }
        }
    }

    // ---- watchlist tab: centered title + Movies / Series sections ----
    // A poster count small enough to curate by hand never needs delegate
    // recycling, so plain Repeaters in a Flickable beat two GridViews here:
    // both sections scroll as one page.
    Flickable {
        id: watchlistFlick
        // Wheel scrolling that doesn't eat the click after it (see the component).
        WheelScroller { flick: watchlistFlick }
        visible: home.watchlistMode && moviesRep.count + seriesRep.count > 0
        maximumFlickVelocity: 12000
        flickDeceleration: 8000
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 0
        anchors.bottomMargin: 24
        // Content inset (not an anchor margin): the header rests below the
        // floating bar, but scrolled content slides underneath it.
        topMargin: 92
        contentWidth: width
        contentHeight: watchlistContent.implicitHeight + 24
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: AppScrollBar {}
        onMovementStarted: watchlistToTop.stop()

        Column {
            id: watchlistContent
            width: watchlistFlick.width - 24
            spacing: 28

            // Page title, centered — the tab has no catalog rows to fill the
            // space, so it names itself instead.
            Column {
                width: parent.width
                spacing: 6
                AppIcon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    glyph: Icons.bookmark
                    font.pixelSize: 30
                    color: "#A855F7"
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: "Your Watchlist"
                    color: Theme.text
                    font.pixelSize: 30
                    font.bold: true
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: {
                        var parts = []
                        if (moviesRep.count > 0)
                            parts.push(moviesRep.count + (moviesRep.count === 1 ? " movie" : " movies"))
                        if (seriesRep.count > 0)
                            parts.push(seriesRep.count + (seriesRep.count === 1 ? " series" : " series"))
                        return parts.join(" · ")
                    }
                    color: Theme.textDim
                    font.pixelSize: Theme.fontBody
                }
            }

            // Movies section
            Column {
                width: parent.width
                spacing: 8
                visible: moviesRep.count > 0
                Row {
                    spacing: 10
                    AppIcon {
                        anchors.verticalCenter: parent.verticalCenter
                        glyph: Icons.theaters
                        font.pixelSize: Theme.fontTitle
                        color: "#3B82F6"
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Movies"
                        color: "white"
                        font.pixelSize: 18
                        font.bold: true
                    }
                }
                Flow {
                    width: parent.width
                    spacing: 16
                    Repeater {
                        id: moviesRep
                        model: watchlistMoviesModel
                        delegate: PosterCard {
                            // A little wider than the 160px card so the hover
                            // scale-up grows into the slack, not the clip edge.
                            width: 176
                            height: 300
                            // The page's visible bounds in this card's
                            // coordinates: posters dissolve into the window's
                            // top and bottom edges instead of being sliced by
                            // the Flickable's clip, the same feather the
                            // catalog rows get. mapToItem is not a tracked
                            // binding dependency, so contentY (scrolling) and
                            // width (relayout) are read to drive it.
                            readonly property real pageY: {
                                const _scroll = watchlistFlick.contentY
                                const _layout = watchlistFlick.width
                                return mapToItem(watchlistFlick, 0, 0).y
                            }
                            edgeClipTop: -pageY
                            edgeClipBottom: watchlistFlick.height - pageY
                            title: model.name
                            posterUrl: model.poster ? model.poster : ""
                            posterShape: model.posterShape
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
                            onClicked: home.openDetail(model.type, model.id)
                        }
                    }
                }
            }

            // Series section
            Column {
                width: parent.width
                spacing: 8
                visible: seriesRep.count > 0
                Row {
                    spacing: 10
                    AppIcon {
                        anchors.verticalCenter: parent.verticalCenter
                        glyph: Icons.liveTv
                        font.pixelSize: Theme.fontTitle
                        color: "#22C55E"
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Series"
                        color: "white"
                        font.pixelSize: 18
                        font.bold: true
                    }
                }
                Flow {
                    width: parent.width
                    spacing: 16
                    Repeater {
                        id: seriesRep
                        model: watchlistSeriesModel
                        delegate: PosterCard {
                            width: 176
                            height: 300
                            // The page's visible bounds in this card's
                            // coordinates: posters dissolve into the window's
                            // top and bottom edges instead of being sliced by
                            // the Flickable's clip, the same feather the
                            // catalog rows get. mapToItem is not a tracked
                            // binding dependency, so contentY (scrolling) and
                            // width (relayout) are read to drive it.
                            readonly property real pageY: {
                                const _scroll = watchlistFlick.contentY
                                const _layout = watchlistFlick.width
                                return mapToItem(watchlistFlick, 0, 0).y
                            }
                            edgeClipTop: -pageY
                            edgeClipBottom: watchlistFlick.height - pageY
                            title: model.name
                            posterUrl: model.poster ? model.poster : ""
                            posterShape: model.posterShape
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
                            onClicked: home.openDetail(model.type, model.id)
                        }
                    }
                }
            }
        }
    }
    Item {
        anchors.fill: watchlistFlick
        visible: watchlistFlick.visible
        WheelHandler {
            acceptedDevices: PointerDevice.Mouse
            onWheel: (w) => {
                watchlistToTop.stop()
                Scroll.wheel(watchlistFlick, w)
            }
        }
    }

    Column {
        anchors.centerIn: parent
        spacing: 12
        visible: home.watchlistMode && moviesRep.count + seriesRep.count === 0
        AppIcon {
            anchors.horizontalCenter: parent.horizontalCenter
            glyph: Icons.bookmarkBorder
            font.pixelSize: 56
            color: Theme.borderStrong
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Your watchlist is empty"
            color: Theme.text
            font.pixelSize: Theme.fontTitle
            font.bold: true
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Add titles from their page or a poster's right-click menu."
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
    }

    // Boot overlay: one big spinner while the first load assembles the page.
    Column {
        anchors.centerIn: parent
        spacing: 16
        visible: home.booting && !home.watchlistMode
        AppSpinner {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 44; height: 44
            running: home.booting
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Loading your library…"
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
    }

    AppSpinner {
        id: busy
        anchors.centerIn: parent
        // The boot overlay owns startup; this one covers later refreshes
        // (installing an addon re-runs the catalog load).
        running: false
        visible: running && !home.booting
        Connections {
            target: catalogController
            function onLoadingChanged(loading) { busy.running = loading }
        }
    }
}
