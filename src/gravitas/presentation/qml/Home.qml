import QtQuick
import QtQuick.Controls
import "components"

Item {
    id: home
    objectName: "homePage"
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

    ListView {
        id: rowsView
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
        // Pre-build rows well below the fold so their posters are fetched
        // and decoded before the user scrolls to them — scrolling should
        // reveal finished cards, not loading spinners. Delegate creation is
        // spread over frames by the view, so this does not stall the reveal.
        cacheBuffer: 6000
        // Recycle row delegates that do scroll out instead of destroying and
        // re-instantiating them — creation cost is the other half of scroll
        // stutter.
        reuseItems: true
        ScrollBar.vertical: AppScrollBar {}
        model: catalogRowsModel
        // Each row loads through an ASYNC Loader: when a model change lands,
        // the view would otherwise create every visible row's strip — and
        // each strip its visible cards — in one synchronous chunk on the GUI
        // thread, stalling the render sync (the boot spinner hitched on
        // every such burst). The incubator spreads that creation over
        // frames instead. The Loader carries the strip's fixed geometry
        // (28px header + 8px gap + 300px cards) so row layout is stable
        // before its content exists.
        delegate: Loader {
            width: rowsView.width - 24
            height: 336
            asynchronous: true
            sourceComponent: CatalogRowStrip {
                width: rowsView.width - 24
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
            onWheel: (w) => Scroll.wheel(rowsView, w)
        }
    }

    // ---- watchlist tab: centered title + Movies / Series sections ----
    // A poster count small enough to curate by hand never needs delegate
    // recycling, so plain Repeaters in a Flickable beat two GridViews here:
    // both sections scroll as one page.
    Flickable {
        id: watchlistFlick
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
            onWheel: (w) => Scroll.wheel(watchlistFlick, w)
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
