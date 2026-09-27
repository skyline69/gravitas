pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects
import Gravitas
import "components"

Item {
    id: detail
    property string mediaType
    property string mediaId
    signal playUrl(string url, var headers)

    // The source row clicked while the list was the stored one, waiting for
    // its fresh link; -1 when none is.
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
        // knows its release.
        PlayerController.setSourceLabel(source.name, source.title)
        PlayerController.setSourceFile(source.filename)
        // What to fall back to if this source's host refuses the connection,
        // which takes every source on that CDN node with it and says nothing
        // about the title.
        PlayerController.setSourceQueue(DetailController.sourceQueue(row))
        detail.playUrl(source.url, source.headers)
    }

    Connections {
        target: DetailController
        function onFreshSourceReady(row: int) {
            if (detail.pendingRow < 0)
                return
            detail.pendingRow = -1
            if (row >= 0)
                detail.playRow(row)
        }
    }
    signal back()
    signal openSources()

    // Fades itself in; StackView's transitions are empty so the stack stays
    // responsive during the animation (see PageFade).
    opacity: 0
    PageFade { id: pageFade; target: detail }
    StackView.onActivating: pageFade.restart()
    Component.onCompleted: {
        pageFade.restart()
        detail._ready = true
        detail._load()
    }

    // The load waits for the page to be complete, because it needs BOTH
    // properties and nothing orders how they arrive. A page pushed with
    // {mediaType, mediaId} gets them as a map, and a map may hand them over
    // sorted -- mediaId first -- so loading from onMediaIdChanged read a
    // mediaType that was still "" and opened every series as a movie, with no
    // episode list. Seen from a harness (createWithInitialProperties sorts);
    // StackView happened to set the type first. After completion a new id
    // still reloads, with the type it arrived alongside.
    property bool _ready: false
    function _load() {
        if (detail.mediaId.length)
            DetailController.load(detail.mediaType, detail.mediaId)
    }
    onMediaIdChanged: if (detail._ready) detail._load()

    // True only once the controller's ratings describe THIS item. A Detail
    // page is built before load() (an asyncSlot) has run, so until then the
    // controller still holds the item the user was looking at a moment ago --
    // bind to it and the previous film's pill is on screen instantly, then
    // fades out through a bare "%" as the reset lands. Waiting for the ids to
    // agree means the pills only ever animate in with their numbers already
    // in them.
    readonly property bool ratingsReady:
        DetailController && DetailController.ratingsFor === detail.mediaId

    // The same guard for the page itself. Until THIS title's meta is in, the
    // content stays laid out but invisible -- an empty shell of "Add to
    // watchlist" and a Sources header, then everything popping in at once,
    // is what a slow meta fetch used to look like -- and fades in whole.
    readonly property bool metaReady:
        DetailController !== null && DetailController.metaFor === detail.mediaId
    readonly property bool metaFailed:
        DetailController !== null && DetailController.metaFailedFor === detail.mediaId
    // The throbber waits before it shows: a cached title's meta lands in tens
    // of milliseconds, and a spinner that flashes for two frames on every
    // open is noise, not feedback. Only a load that is actually slow gets one.
    property bool slowLoad: false
    Timer {
        interval: 200
        running: !detail.metaReady && !detail.metaFailed
        onTriggered: detail.slowLoad = true
    }

    // What the clicked card showed (DetailController.preview): its title and
    // poster, for the page to stand on while a slow meta arrives. Like the
    // meta, only ever read when it names THIS title.
    readonly property bool hasPreview:
        DetailController !== null && DetailController.previewFor === detail.mediaId
    // Shown with the throbber, not at once: on a cached title the meta lands
    // in tens of milliseconds, and a preview title crossfading into the real
    // logo two frames later reads as a flicker.
    readonly property bool showPreview:
        detail.hasPreview && detail.slowLoad && !detail.metaReady && !detail.metaFailed

    // blurred background art + dark scrim for readability. Two layers: the
    // card's poster stands in while the meta loads, and the title's own
    // background fades in over it once it has decoded -- a source swap on one
    // layer would dip to black between the two.
    Image {
        id: previewBgSrc
        anchors.fill: parent
        source: detail.hasPreview ? Img.sized(DetailController.previewPoster, 342) : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        visible: false
    }
    FastBlur {
        anchors.fill: parent
        source: previewBgSrc
        radius: 64
        // Retired once the real art is fully in, so two full-window blurs do
        // not run for the life of the page.
        visible: opacity > 0 && bgBlur.opacity < 1
        opacity: detail.showPreview && previewBgSrc.status === Image.Ready ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
    }
    Image {
        id: bgSrc
        anchors.fill: parent
        source: detail.metaReady && DetailController.background ? DetailController.background : ""
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        visible: false
    }
    FastBlur {
        id: bgBlur
        anchors.fill: parent
        source: bgSrc
        radius: 64
        // fade the art in once it has decoded instead of popping in
        opacity: bgSrc.status === Image.Ready ? 1.0 : 0.0
        Behavior on opacity { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
    }
    Rectangle { anchors.fill: parent; color: Qt.rgba(0.078, 0.078, 0.078, 0.86) }

    // Throbber for a slow meta fetch (see slowLoad).
    AppSpinner {
        anchors.centerIn: parent
        width: 44; height: 44
        running: detail.slowLoad && !detail.metaReady && !detail.metaFailed
    }

    // The card's title, where the page's own title will be: same box, same
    // type, so the real one (or its logo) takes over in place as the content
    // fades in over it.
    Item {
        x: 24
        y: 24
        width: parent.width - 48
        height: 120
        opacity: detail.showPreview ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed * 2; easing.type: Easing.OutCubic } }
        Text {
            anchors.centerIn: parent
            width: parent.width
            text: detail.hasPreview ? DetailController.previewName : ""
            color: Theme.text
            font.pixelSize: 32
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }

    // The meta could not be loaded: say so where the page would have been,
    // with a way to try again, instead of leaving an empty shell under a
    // toast that is gone in four seconds.
    Column {
        anchors.centerIn: parent
        spacing: 16
        visible: opacity > 0
        opacity: detail.metaFailed ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Couldn't load this title."
            color: Theme.textDim
            font.pixelSize: Theme.fontBody
        }
        AppButton {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Try again"
            onClicked: DetailController.load(detail.mediaType, detail.mediaId)
        }
    }

    // floating back button, above the scrolling content
    BackButton {
        z: 10
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: 16
        blurTarget: flick
        onClicked: detail.back()
    }

    // A series is not known to list its sources inline until its meta says it
    // has no episodes, so until then it shows neither the section nor its
    // loading rows -- otherwise every series opened on a flash of "Sources"
    // that the episode list then replaced.
    readonly property bool inlineSources: detail.mediaType !== "series"
        || (DetailController !== null && DetailController.hasMeta
            && !DetailController.hasEpisodes)

    // The page is a ListView, not a Flickable around a Column: the metadata is
    // its header and the sources are its delegates, so only the rows on screen
    // exist. An aggregator answers with 90-150 sources of ~20 items each, and
    // keeping all of them alive cost twice -- once to build (a Repeater builds
    // every delegate in the frame the model resets: 150-250ms of blocked GUI
    // thread, measured) and again on every frame after, because the back
    // button's frosted glass re-renders its whole blur target whenever any of
    // it changes. With 145 rows that was a p95 of 29ms per frame on the render
    // thread while scrolling or hovering (RTX 2070, measured); only the rows
    // in view is what the page actually shows.
    ListView {
        id: flick
        // Stays in the scene at opacity 0 while loading, so the layout and the
        // first source rows are built behind the throbber rather than in the
        // reveal frame.
        opacity: detail.metaReady ? 1 : 0
        enabled: detail.metaReady
        Behavior on opacity { NumberAnimation { duration: Theme.durMed * 2; easing.type: Easing.OutCubic } }
        // Wheel scrolling that doesn't eat the click after it (see the component).
        WheelScroller { flick: flick }
        anchors.fill: parent
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        // The page dissolves into the window's top and bottom edges as it
        // scrolls (see EdgeFade); the bar lives outside the view to stay crisp.
        // Not on the software renderer: it has no shaders, and the effect
        // would draw nothing at all where the view should be.
        layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
        layer.effect: EdgeFade { view: flick }
        ScrollBar.vertical: AppScrollBar {
            parent: flick.parent
            anchors.top: flick.top
            anchors.bottom: flick.bottom
            anchors.right: flick.right
        }
        spacing: 8
        // Bound only once THIS title's meta is in. The models are shared by
        // every Detail page and still hold the previous title's rows when a
        // new page is built: bound at once, the view started building
        // delegates for them, and load()'s reset then cancelled those
        // mid-creation ("DelegateModel::cancel: index out range").
        model: detail.metaReady && detail.inlineSources ? StreamModel : null
        // Rows fly in when the list fills from empty -- page open, first answer,
        // a new episode -- and not when scrolling builds more of them later. Armed
        // while empty; disarmed shortly after the first rows exist, which covers
        // every delegate built in that fill whatever order count and creation run in.
        property bool flyInArmed: true
        onCountChanged: {
            if (flick.count === 0)
                flick.flyInArmed = true
            else if (flick.flyInArmed)
                flyInDisarm.restart()
        }
        Timer { id: flyInDisarm; interval: 300; onTriggered: flick.flyInArmed = false }

        header: Item {
            width: ListView.view.width
            height: content.implicitHeight + 24 + 8

            Column {
                id: content
                x: 24
                y: 24
                width: parent.width - 48
                spacing: 16

                // title logo art with a bold-text fallback: the title shows while
                // the logo loads, then the two crossfade (title stays if no logo)
                Item {
                    width: parent.width
                    height: 120
                    Image {
                        id: logo
                        anchors.centerIn: parent
                        height: 110
                        fillMode: Image.PreserveAspectFit
                        source: DetailController && DetailController.logo ? DetailController.logo : ""
                        asynchronous: true
                        opacity: status === Image.Ready ? 1.0 : 0.0
                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                    }
                    Text {
                        anchors.centerIn: parent
                        text: DetailController ? DetailController.title : ""
                        color: Theme.text
                        font.pixelSize: 32
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        opacity: logo.status === Image.Ready ? 0.0 : 1.0
                        Behavior on opacity { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                    }
                }

                // meta row: runtime · year · rating + IMDb badge
                Row {
                    spacing: 24
                    Text {
                        visible: text.length > 0
                        text: DetailController ? DetailController.runtime : ""
                        color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                    }
                    Text {
                        visible: text.length > 0
                        text: DetailController ? DetailController.year : ""
                        color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                    }
                    Row {
                        spacing: 8
                        visible: ratingText.text.length > 0
                        Text {
                            id: ratingText
                            anchors.verticalCenter: parent.verticalCenter
                            text: DetailController ? DetailController.imdbRating : ""
                            color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                        }
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: badge.implicitWidth + 12
                            height: 22
                            radius: 4
                            color: "#f5c518"
                            Text {
                                id: badge
                                anchors.centerIn: parent
                                text: "IMDb"
                                color: "#000000"
                                font.pixelSize: Theme.fontSmall
                                font.bold: true
                            }
                        }
                    }
                    Row {
                        id: rtRow
                        spacing: 8
                        // Ratings arrive after the page (and may be cached, so nearly
                        // instant); fade + slide the pill in so it never just pops.
                        property bool shown: detail.ratingsReady
                            && DetailController.rottenTomatoes.length > 0
                        opacity: shown ? 1 : 0
                        visible: shown || opacity > 0
                        Behavior on opacity { NumberAnimation { duration: 550; easing.type: Easing.OutCubic } }
                        transform: Translate {
                            y: rtRow.shown ? 0 : 10
                            Behavior on y { NumberAnimation { duration: 550; easing.type: Easing.OutCubic } }
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            // Suffixed only when there is a number to suffix, so a
                            // lone "%" cannot show through for a frame.
                            text: rtRow.shown ? DetailController.rottenTomatoes + "%" : ""
                            color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                        }
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: rtBadge.implicitWidth + 12
                            height: 22
                            radius: 4
                            color: DetailController && DetailController.rottenTomatoesFresh ? "#fa320a" : "#00a000"
                            Text {
                                id: rtBadge
                                anchors.centerIn: parent
                                text: "RT"
                                color: "#ffffff"
                                font.pixelSize: Theme.fontSmall
                                font.bold: true
                            }
                        }
                    }
                    Row {
                        id: lbRow
                        spacing: 8
                        property bool shown: detail.ratingsReady
                            && DetailController.letterboxd.length > 0
                        opacity: shown ? 1 : 0
                        visible: shown || opacity > 0
                        Behavior on opacity { NumberAnimation { duration: 550; easing.type: Easing.OutCubic } }
                        transform: Translate {
                            y: lbRow.shown ? 0 : 10
                            Behavior on y { NumberAnimation { duration: 550; easing.type: Easing.OutCubic } }
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: lbRow.shown ? DetailController.letterboxd : ""
                            color: Theme.text; font.pixelSize: Theme.fontTitle; font.bold: true
                        }
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: lbBadge.implicitWidth + 12
                            height: 22
                            radius: 4
                            color: "#14181c"
                            Row {
                                id: lbBadge
                                anchors.centerIn: parent
                                spacing: 4
                                Rectangle { width: 8; height: 8; radius: 4; color: "#ff8000"; anchors.verticalCenter: parent.verticalCenter }
                                Rectangle { width: 8; height: 8; radius: 4; color: "#00e054"; anchors.verticalCenter: parent.verticalCenter }
                                Rectangle { width: 8; height: 8; radius: 4; color: "#40bcf4"; anchors.verticalCenter: parent.verticalCenter }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "Letterboxd"
                                    color: "#ffffff"
                                    font.pixelSize: Theme.fontSmall
                                    font.bold: true
                                }
                            }
                        }
                    }
                }

                Row {
                    spacing: 8

                    AppButton {
                        // contains() is a Slot, not a binding dependency — reading
                        // `revision` is what makes this re-evaluate on toggle.
                        readonly property bool inList: WatchlistController
                            && WatchlistController.revision >= 0
                            && WatchlistController.contains(detail.mediaId)
                        text: inList ? "In watchlist" : "Add to watchlist"
                        ghost: true
                        iconGlyph: inList ? Icons.bookmark : Icons.bookmarkAdd
                        tone: inList ? "accent" : "neutral"
                        onClicked: WatchlistController.toggle({
                            mediaId: detail.mediaId,
                            type: detail.mediaType,
                            name: DetailController ? DetailController.title : "",
                            poster: DetailController ? DetailController.poster : "",
                            year: DetailController ? DetailController.year : ""
                        })
                    }

                    AppButton {
                        text: "Forget progress"
                        ghost: true
                        iconGlyph: Icons.trash
                        tone: "negative"
                        // hasProgress() is a Slot, not a binding dependency — reading
                        // `revision` is what makes this re-evaluate when it changes.
                        visible: ProgressController
                            && ProgressController.revision >= 0
                            && ProgressController.hasProgress(detail.mediaId)
                        onClicked: forgetDialog.ask()
                    }
                }

                ConfirmDialog {
                    id: forgetDialog
                    heading: "Forget progress for "
                        + (DetailController ? DetailController.title : "this title") + "?"
                    body: detail.mediaType === "series"
                        ? "Every episode's saved position is cleared. This cannot be undone."
                        : "The saved position is cleared. This cannot be undone."
                    confirmText: "Forget"
                    onConfirmed: ProgressController.forgetMedia(detail.mediaId)
                }

                // description
                Text {
                    width: parent.width
                    text: DetailController ? DetailController.description : ""
                    color: Theme.text
                    opacity: 0.9
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontBody
                }

                // genres
                Column {
                    width: parent.width
                    spacing: 8
                    visible: genresRep.count > 0
                    Text { text: "GENRES"; color: Theme.textDim; font.pixelSize: Theme.fontSmall; font.bold: true }
                    Flow {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            id: genresRep
                            model: DetailController ? DetailController.genres : []
                            AppChip {
                                required property string modelData
                                text: modelData
                            }
                        }
                    }
                }

                // cast
                Column {
                    width: parent.width
                    spacing: 8
                    visible: castRep.count > 0
                    Text { text: "CAST"; color: Theme.textDim; font.pixelSize: Theme.fontSmall; font.bold: true }
                    Flow {
                        width: parent.width
                        spacing: 8
                        Repeater {
                            id: castRep
                            model: DetailController ? DetailController.cast : []
                            AppChip {
                                required property string modelData
                                text: modelData
                            }
                        }
                    }
                }

                // directors
                Text {
                    width: parent.width
                    visible: DetailController && DetailController.directors && DetailController.directors.length > 0
                    text: (DetailController && DetailController.directors)
                        ? "Directed by " + DetailController.directors.join(", ")
                        : ""
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }

                // writers
                Text {
                    width: parent.width
                    visible: DetailController && DetailController.writers && DetailController.writers.length > 0
                    text: (DetailController && DetailController.writers)
                        ? "Written by " + DetailController.writers.join(", ")
                        : ""
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }

                // trailer — only when the addon carries one. Plays through mpv's
                // ytdl_hook, so it needs yt-dlp present; failure surfaces as the
                // usual playback error rather than a dead button.
                AppButton {
                    visible: DetailController && DetailController.trailerUrl.length > 0
                    text: "Play trailer"
                    ghost: true
                    // A trailer is not the title: without clearing the media
                    // context, the controller resumes the trailer from wherever
                    // the LAST watched thing stopped, records the trailer's
                    // position over that title's progress every 5s, and
                    // scrobbles the trailer to Trakt as that title.
                    onClicked: {
                        PlayerController.setMediaContext({})
                        detail.playUrl(DetailController.trailerUrl, ({}))
                    }
                }

                // episodes (series with videos only)
                Column {
                    id: episodesSection
                    width: parent.width
                    spacing: 12
                    visible: detail.mediaType === "series"
                        && (episodesList.count > 0 || episodeSearch.searching)

                    Item {
                        width: parent.width
                        height: seasonBox.height
                        Text {
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Episodes"
                            color: Theme.text
                            font.pixelSize: 20
                        }
                        // Searches every season, not the one picked: the point
                        // is not knowing which season it was.
                        AppTextField {
                            id: episodeSearch
                            readonly property bool searching: text.trim().length > 0
                            anchors.right: seasonBox.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            width: Math.min(280, parent.width - seasonBox.width - 160)
                            leftPadding: searchIcon.width + Theme.spacing * 2
                            rightPadding: clearSearch.visible
                                ? clearSearch.width + Theme.spacing * 1.5 : Theme.spacing * 1.5
                            placeholderText: "Search episodes"
                            text: DetailController ? DetailController.episodeQuery : ""
                            onTextEdited: DetailController.setEpisodeQuery(text)
                            // Esc clears a search first; an empty box lets it through.
                            Keys.onEscapePressed: (event) => {
                                if (text.length > 0)
                                    DetailController.setEpisodeQuery("")
                                else
                                    event.accepted = false
                            }
                            AppIcon {
                                id: searchIcon
                                anchors.left: parent.left
                                anchors.leftMargin: Theme.spacing
                                anchors.verticalCenter: parent.verticalCenter
                                glyph: Icons.search
                                font.pixelSize: Theme.fontBody
                                color: Theme.textDim
                            }
                            AppIcon {
                                id: clearSearch
                                anchors.right: parent.right
                                anchors.rightMargin: Theme.spacing
                                anchors.verticalCenter: parent.verticalCenter
                                visible: episodeSearch.text.length > 0
                                glyph: Icons.x
                                font.pixelSize: Theme.fontBody
                                color: clearArea.containsMouse ? Theme.text : Theme.textDim
                                MouseArea {
                                    id: clearArea
                                    anchors.fill: parent
                                    anchors.margins: -4
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: DetailController.setEpisodeQuery("")
                                }
                            }
                        }
                        AppComboBox {
                            id: seasonBox
                            anchors.right: parent.right
                            model: DetailController ? DetailController.seasonOptions : []
                            currentIndex: DetailController ? DetailController.seasonIndex : 0
                            onActivated: (index) => { if (DetailController) DetailController.selectSeason(index) }
                        }
                    }

                    // The episode list scrolls by itself, in whatever height the
                    // page has left below the metadata, so the title, ratings and
                    // season picker stay in view while a long season scrolls. A
                    // window too short for that still keeps a few rows, and the
                    // page itself scrolls to reach them.
                    Item {
                        width: parent.width
                        height: noMatch.visible ? noMatch.height : episodesList.height

                        Text {
                            id: noMatch
                            visible: episodeSearch.searching && episodesList.count === 0
                            width: parent.width
                            topPadding: 8
                            bottomPadding: 8
                            text: "No episode matches \u201c" + episodeSearch.text.trim() + "\u201d"
                            color: Theme.textDim
                            font.pixelSize: Theme.fontBody
                            elide: Text.ElideRight
                        }

                        ListView {
                            id: episodesList
                            // The page's own height, less everything above this
                            // list and the header's and footer's padding (24 + 8
                            // + 24): exactly what fits without the page scrolling.
                            readonly property real room: flick.height - 24 - 8 - 24
                                - episodesSection.y - seasonBox.height - episodesSection.spacing
                            width: parent.width
                            height: Math.min(contentHeight, Math.max(280, room))
                            clip: true
                            spacing: 8
                            boundsBehavior: Flickable.StopAtBounds
                            WheelScroller { flick: episodesList }
                            layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
                            layer.effect: EdgeFade { view: episodesList }
                            // In the page's right margin, outside the feathered view.
                            ScrollBar.vertical: AppScrollBar {
                                parent: episodesList.parent
                                anchors.top: episodesList.top
                                anchors.bottom: episodesList.bottom
                                anchors.left: episodesList.right
                                anchors.leftMargin: 4
                            }
                            // Same reason as the source list's model above.
                            model: !detail.metaReady ? null
                                : (episodeSearch.searching ? EpisodeSearchModel : EpisodeModel)
                            delegate: EpisodeRow {
                                required property var model
                                width: ListView.view.width
                                showSeason: episodeSearch.searching
                                title: model.title
                                thumbnailUrl: model.thumbnail
                                seasonNumber: model.season
                                episodeNumber: model.episode
                                overview: model.overview
                                released: model.released
                                active: DetailController
                                    && DetailController.selectedEpisodeId === model.videoId
                                progressFraction: model.progressFraction
                                watched: model.watched
                                forgetContext: ({
                                    mediaId: detail.mediaId,
                                    videoId: model.videoId,
                                    type: "series",
                                    name: DetailController ? DetailController.title : "",
                                    poster: DetailController ? DetailController.poster : "",
                                    label: "S" + model.season + "E" + model.episode
                                        + (model.title ? " · " + model.title : "")
                                })
                                onIntent: DetailController.prefetchEpisode(model.videoId)
                                onClicked: {
                                    DetailController.selectEpisode(
                                        model.videoId, model.season, model.episode, model.title)
                                    detail.openSources()
                                }
                            }
                        }
                    }
                }

                // sources — inline for movies only; episode sources open on their
                // own page (pushed when an episode row is clicked)
                Row {
                    spacing: 12
                    visible: detail.inlineSources
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Sources"
                        color: Theme.text
                        font.pixelSize: 20
                    }
                    AppSpinner {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 18; height: 18
                        running: DetailController ? DetailController.streamsLoading : false
                    }
                }
                Column {
                    width: parent.width
                    spacing: 8
                    visible: detail.inlineSources
                    SourcesEmptyState {
                        count: flick.count
                        loading: DetailController !== null && DetailController.streamsLoading
                        failed: DetailController !== null && DetailController.sourcesFailed
                        onRetry: DetailController.retrySources()
                    }

                    // Same offer as the per-episode page: say what was held back,
                    // and let the user overrule it.
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
                    Repeater {
                        model: (flick.count === 0
                            && DetailController && DetailController.streamsLoading) ? 4 : 0
                        StreamRowSkeleton {
                            required property int index
                            width: content.width
                            pulseDelay: index * 90
                        }
                    }
                }
            }
        }

        footer: Item { width: ListView.view.width; height: 24 }

        delegate: Item {
            id: rowItem
            required property int index
            required property var model
            width: ListView.view.width
            height: sourceRow.height
            Component.onCompleted: if (flick.flyInArmed) sourceRow.flyIn(Math.min(index, 10) * 45)
            StreamRow {
                id: sourceRow
                x: 24
                width: parent.width - 48
                name: rowItem.model.name
                subtitle: rowItem.model.subtitle
                resolution: rowItem.model.resolution
                instant: rowItem.model.instant
                tags: rowItem.model.tags
                stars: rowItem.model.stars
                detailText: rowItem.model.extra
                overBudget: rowItem.model.overBudget
                oversized: rowItem.model.oversized
                // The movie page lists sources inline under the rest of the
                // metadata, so it carries the chip and the earned order but no
                // section rule — a divider inside a page that is already
                // divided reads as a new page.
                recommended: rowItem.model.recommended
                reason: rowItem.model.reason
                pending: detail.pendingRow === rowItem.index
                onClicked: {
                    // externalUrl streams are a web page, not a media file:
                    // mpv can do nothing with them.
                    if (rowItem.model.external) {
                        PlayerController.openExternal(rowItem.model.external)
                        return
                    }
                    if (!rowItem.model.url)
                        return
                    // A stored list's links may be from another session
                    // (bound to an address this machine no longer has, or
                    // expired): wait for the fresh list's link to the same
                    // release.
                    if (DetailController.sourcesStored) {
                        detail.pendingRow = rowItem.index
                        DetailController.pickWhenFresh(rowItem.index)
                        return
                    }
                    detail.playRow(rowItem.index)
                }
            }
        }
    }
}
