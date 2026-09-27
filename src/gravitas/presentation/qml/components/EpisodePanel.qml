pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import Qt5Compat.GraphicalEffects
import Gravitas
import "."

// The player's episode panel: the episode on screen at the top (still, title,
// overview), then search and season, then every episode of that season with
// the playing one outlined. Clicking another episode fetches its sources and
// asks the player to start the best one (playFirstSource).
//
// It reads the series from DetailController and EpisodeModel, which still hold
// it: the player is only ever reached from that series' Detail page. Changing
// season here changes it there too, which is what going back should show.
Item {
    id: panel
    property bool open: false
    // The episode whose sources are being fetched after a click; "" when none.
    property string pendingVideoId: ""
    // Why the last click could not play, shown under the search row.
    property string message: ""
    // The player's hotkeys fire regardless of focus, so they are switched off
    // while this is true -- otherwise typing "jack" would seek and pause. Set
    // from inside the list's header (a Binding there), whose ids are not
    // visible out here.
    property bool searchFocused: false
    // Built the first time the panel opens and kept: most playbacks never
    // open it, and a series' header and rows are not free.
    property bool _built: false
    signal closeRequested()
    // Row 0 of DetailController's list is ready: the player plays it, with the
    // rest as its fallback queue.
    signal playFirstSource()

    // Slide + fade, as one 0..1 value so the player can move its button with
    // the panel's edge. The panel travels its whole width, so it arrives from
    // off-screen rather than popping in place.
    property real reveal: open ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.durMed * 1.6; easing.type: Easing.OutCubic } }
    opacity: reveal
    visible: reveal > 0
    transform: Translate { x: (1 - panel.reveal) * (panel.width + 12) }

    // The string search matches against: case- and accent-insensitive, and an
    // episode number on its own ("6", "e6") finds that episode.
    property string _query: ""
    function _fold(text: string): string {
        return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    }
    function matches(title: string, episode: int): bool {
        const query = panel._fold(panel._query.trim())
        if (!query)
            return true
        if (query === String(episode) || query === "e" + episode)
            return true
        return panel._fold(title).includes(query)
    }

    function pick(videoId: string, season: int, episode: int, title: string) {
        if (!DetailController || !PlayerController)
            return
        if (videoId === PlayerController.videoId) {
            panel.closeRequested()
            return
        }
        panel.message = ""
        panel.pendingVideoId = videoId
        DetailController.playEpisode(videoId, season, episode, title)
    }

    // Opened on the playing episode's season, whatever the Detail page was
    // left showing, with the search cleared and that episode in view.
    // The header's search field resets itself on this (its id is not
    // reachable from here).
    signal reset()
    onOpenChanged: {
        if (!panel.open) {
            panel.reset()
            return
        }
        panel._built = true
        panel.message = ""
        panel._query = ""
        panel.reset()
        if (!DetailController || !PlayerController)
            return
        const season = DetailController.seasonIndexOf(PlayerController.videoId)
        if (season >= 0 && season !== DetailController.seasonIndex)
            DetailController.selectSeason(season)
        Qt.callLater(panel._showPlaying)
    }
    // The episode list, set from inside the content once it is built.
    property ListView listView: null
    function _showPlaying() {
        if (!panel.listView || !DetailController || !PlayerController)
            return
        const row = DetailController.episodeRowOf(PlayerController.videoId)
        if (row >= 0)
            panel.listView.positionViewAtIndex(row, ListView.Contain)
    }

    Connections {
        target: DetailController
        function onEpisodeReadyToPlay(videoId: string) {
            if (videoId !== panel.pendingVideoId)
                return
            panel.pendingVideoId = ""
            panel.playFirstSource()
        }
        function onEpisodeUnplayable(videoId: string, reason: string) {
            if (videoId !== panel.pendingVideoId)
                return
            panel.pendingVideoId = ""
            panel.message = reason
        }
    }

    // A soft shadow under the sheet, so the panel reads as lifted off the
    // film rather than pasted onto it. On the sheet alone, not the content:
    // the sheet never changes, so its blurred copy is made once and kept,
    // where a shadow over the episode rows would be redrawn on every scroll.
    Rectangle {
        id: sheet
        anchors.fill: parent
        radius: 14
        color: Qt.rgba(0.078, 0.078, 0.078, 0.94)
        border.width: 1
        border.color: Theme.border
        layer.enabled: true
        layer.effect: DropShadow {
            horizontalOffset: -2
            verticalOffset: 6
            radius: 28
            samples: 57
            color: Qt.rgba(0, 0, 0, 0.55)
            transparentBorder: true
        }
    }

    // The panel is not the video: clicks and wheel stop here instead of
    // pausing playback underneath, and the pointer stays visible over it
    // while the player's controls are hidden.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
        cursorShape: Qt.ArrowCursor
        onWheel: (wheel) => { wheel.accepted = true }
    }

    Loader {
        id: content
        anchors.fill: parent
        anchors.margins: 12
        active: panel._built
        sourceComponent: panelContent
    }

    // The episode on screen, search and season stay put; only the episode
    // list under them scrolls, with its own scroll bar.
    Component {
        id: panelContent
        Item {
            id: body
            readonly property var playing: DetailController && PlayerController
                ? DetailController.episodeInfo(PlayerController.videoId) : ({})
            // Handed to the panel for _showPlaying (this component's ids are
            // not visible from out there).
            Binding { target: panel; property: "listView"; value: list }

            Column {
                id: head
                width: parent.width
                spacing: 10

                // The episode on screen. Its own still when the addon has one,
                // else the series' backdrop. Capped against the panel's height
                // so a short window still leaves room for the list.
                Item {
                    width: parent.width
                    height: Math.min(Math.round(width * 9 / 16), Math.round(panel.height * 0.26))
                    Rectangle {
                        anchors.fill: parent
                        radius: 10
                        color: Theme.surfaceHover
                    }
                    Image {
                        id: hero
                        anchors.fill: parent
                        source: body.playing.thumbnail
                            ? Img.sized(body.playing.thumbnail, 780)
                            : (DetailController ? DetailController.background : "")
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        opacity: status === Image.Ready ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
                        layer.enabled: true
                        layer.effect: OpacityMask {
                            maskSource: Rectangle { width: hero.width; height: hero.height; radius: 10 }
                        }
                    }
                }
                Text {
                    width: parent.width
                    text: DetailController ? DetailController.title : ""
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    topPadding: -6
                    text: body.playing.label || ""
                    color: Theme.text
                    font.pixelSize: Theme.fontTitle
                    font.bold: true
                    wrapMode: Text.WordWrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    visible: text.length > 0
                    text: body.playing.overview || ""
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                    maximumLineCount: 4
                    elide: Text.ElideRight
                    lineHeight: 1.15
                }

                Row {
                    width: parent.width
                    spacing: 8
                    AppTextField {
                        id: search
                        width: parent.width - seasonBox.width - parent.spacing
                        placeholderText: "Search this season"
                        onTextEdited: panel._query = text
                        // Esc leaves the field first; a second Esc closes the
                        // panel through the player's own shortcut.
                        Keys.onEscapePressed: search.focus = false
                        Binding {
                            target: panel
                            property: "searchFocused"
                            value: search.activeFocus
                        }
                        Connections {
                            target: panel
                            function onReset() {
                                search.text = ""
                                search.focus = false
                            }
                        }
                    }
                    AppComboBox {
                        id: seasonBox
                        sizeCap: 160
                        model: DetailController ? DetailController.seasonOptions : []
                        currentIndex: DetailController ? DetailController.seasonIndex : 0
                        onActivated: (index) => {
                            if (DetailController)
                                DetailController.selectSeason(index)
                        }
                    }
                }
                Text {
                    width: parent.width
                    visible: panel.message.length > 0
                    text: panel.message
                    color: Theme.negative
                    font.pixelSize: Theme.fontSmall
                    wrapMode: Text.WordWrap
                }
            }

            ListView {
                id: list
                WheelScroller { flick: list }
                anchors.top: head.bottom
                anchors.topMargin: 12
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                // Rows dissolve under the search row and at the bottom edge,
                // as they do in the source lists (see EdgeFade). The bar sits
                // in the panel's right padding, outside the feather.
                layer.enabled: GraphicsInfo.api !== GraphicsInfo.Software
                layer.effect: EdgeFade { view: list }
                ScrollBar.vertical: AppScrollBar {
                    parent: list.parent
                    anchors.top: list.top
                    anchors.bottom: list.bottom
                    anchors.left: list.right
                    anchors.leftMargin: 1
                }
                model: DetailController ? EpisodeModel : null

                // Filtered rows collapse to nothing rather than leave the
                // model: the season's list is the one model, and the search is
                // a view over it.
                delegate: Item {
                    id: entry
                    required property var model
                    readonly property bool shown: panel.matches(entry.model.title, entry.model.episode)
                    width: ListView.view.width
                    height: shown ? row.height + 8 : 0
                    visible: shown

                    EpisodeRow {
                        id: row
                        width: parent.width
                        thumbWidth: 112
                        showReleased: false
                        title: entry.model.title
                        thumbnailUrl: entry.model.thumbnail
                        seasonNumber: entry.model.season
                        episodeNumber: entry.model.episode
                        overview: entry.model.overview
                        progressFraction: entry.model.progressFraction
                        watched: entry.model.watched
                        active: !!PlayerController && PlayerController.videoId === entry.model.videoId
                        onClicked: panel.pick(entry.model.videoId, entry.model.season,
                                              entry.model.episode, entry.model.title)
                    }
                    // Finding this episode's sources: dimmed still, spinner.
                    Rectangle {
                        x: 8
                        anchors.verticalCenter: row.verticalCenter
                        width: row.thumbWidth
                        height: row.thumbHeight
                        radius: Theme.radiusSmall
                        color: Qt.rgba(0, 0, 0, 0.55)
                        visible: panel.pendingVideoId === entry.model.videoId
                        AppSpinner {
                            anchors.centerIn: parent
                            width: 22
                            height: 22
                            running: parent.visible
                        }
                    }
                }

                Text {
                    anchors.centerIn: parent
                    visible: list.count > 0 && list.contentHeight < 1
                    text: "No matching episodes"
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }
            }
        }
    }
}
