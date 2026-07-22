import QtQuick
import QtQuick.Window
import "."

Item {
    id: root

    // Recycled-delegate hygiene, for every view this card sits in. A delegate
    // released to the reuse pool by a REMOVAL (filter narrowed, board shrank)
    // is not hidden by the view — it keeps painting at its old slot while the
    // model says it's gone (the "0 / 46 but cards everywhere" filter bug).
    //
    // Derived from the view's own bookkeeping rather than toggled in
    // onPooled/onReused: a view sets a delegate's index to -1 while it sits in
    // the pool and back to a real row when it is handed out again. Two
    // imperative writes racing on one property can settle on the wrong one —
    // pool, reuse and pool again inside a frame and the card stays invisible
    // for the rest of the session, showing an empty page for rows the model
    // still holds. A binding cannot get stuck: it is recomputed from the
    // index, whatever order the events arrive in.
    //
    // Only visibility is derived here. Opacity is left alone because strip
    // delegates bind it (edge dissolve) and a write would sever that binding.
    readonly property int viewIndex: typeof index !== "undefined" ? index : 0
    visible: viewIndex >= 0

    property string title
    property string posterUrl
    property string mediaType: "" // "movie" | "series" — picks the filler icon
    property real progressFraction: 0
    property bool watched: false
    // One line under the title ("S1E3 · Pilot"). `showSubtitle` reserves the
    // line for the whole row — see the note on subtitleLabel below.
    property string subtitle: ""
    property bool showSubtitle: false
    // { mediaId, videoId, type, name, poster, label } — null disables the menu.
    property var forgetContext: null
    signal clicked()
    // The addon's posterShape hint: "poster" (2:3, the protocol default),
    // "landscape" (16:9) or "square". Cropping landscape art into a portrait
    // box is what happens when this is ignored.
    property string posterShape: "poster"
    // Milliseconds to hold off the poster fetch after creation. Rows stagger
    // this by index so a freshly-built strip's images finish one after
    // another instead of landing as one batch of texture uploads (a single
    // >100ms frame on the one-thread render loop). 0 = load immediately;
    // once released it stays released, so a recycled delegate rebinding to a
    // new row loads with no artificial wait.
    property int loadDelay: 0
    // Where the surrounding views' clips slice this card, in item
    // coordinates — the poster feathers to nothing approaching them instead
    // of ending in a hard cut. Defaults park them far away (no feather).
    // The horizontal feathers are additionally gated by their strength
    // (0..1, animatable) so a row can FADE its edge dissolve in and out.
    property real edgeClipLeft: -100000
    property real edgeClipRight: 100000
    property real edgeClipTop: -100000
    property real edgeClipBottom: 100000
    property real edgeFadeLeftStrength: 0
    property real edgeFadeRightStrength: 0
    // The title/subtitle are plain Text, not the poster shader's texture, so
    // the same clips that DISSOLVE the poster slice them mid-glyph — the title
    // cut in half across the bottom of the page. Rather than give the labels a
    // per-pixel feather of their own (a layer + FBO on every card, for two
    // short lines), the whole block fades out over the last `textFadeZone`
    // pixels BEFORE a clip edge reaches it, so a cut never lands on a visible
    // word. Coordinates are this card's, matching edgeClip* above.
    readonly property real textFadeZone: 24
    readonly property real textEdgeOpacity: {
        const clamp01 = (v) => Math.max(0, Math.min(1, v))
        const zone = root.textFadeZone
        const top = content.y + label.y
        const bottom = content.y + (subtitleLabel.visible
            ? subtitleLabel.y + subtitleLabel.height
            : label.y + label.height)
        const left = content.x + label.x
        const right = left + label.width
        var f = Math.min(clamp01((root.edgeClipBottom - bottom) / zone),
                         clamp01((top - root.edgeClipTop) / zone))
        // Horizontal edges carry their geometry unconditionally, so they are
        // gated by the same strengths the shader mixes with — a row that has
        // nothing scrolled off that side has no edge to dissolve into.
        f *= 1 - root.edgeFadeLeftStrength * (1 - clamp01((left - root.edgeClipLeft) / zone))
        f *= 1 - root.edgeFadeRightStrength * (1 - clamp01((root.edgeClipRight - right) / zone))
        return f
    }

    property bool _loadReleased: loadDelay <= 0
    Timer {
        interval: root.loadDelay
        running: !root._loadReleased
        onTriggered: root._loadReleased = true
    }
    readonly property real coverWidth: 160
    readonly property real coverHeight: root.posterShape === "landscape"
        ? Math.round(root.coverWidth * 9 / 16)
        : root.posterShape === "square"
            ? root.coverWidth
            : 220
    width: 160
    height: root.coverHeight + 40

    function openMenu(position) {
        if (!root.forgetContext)
            return
        var items = []
        // Offered for a series too. The grid cannot infer that a show is
        // finished -- it has no episode list -- but it does not need to: the
        // user is saying so, and the repository finishes the episodes it knows
        // were started, so the show badges and leaves Continue Watching rather
        // than keeping a bar. Marking a single EPISODE watched still lives on
        // the Detail page, where the episodes are.
        if (!root.watched) {
            items.push({
                label: "Mark as watched",
                action: () => progressController.markWatched(root.forgetContext)
            })
        }
        if (root.progressFraction > 0 || root.watched) {
            items.push({
                label: "Forget progress",
                action: () => progressController.forgetMedia(root.forgetContext.mediaId)
            })
        }
        // Label resolved at open time, so no revision binding is needed here.
        items.push({
            label: watchlistController.contains(root.forgetContext.mediaId)
                ? "Remove from watchlist" : "Add to watchlist",
            action: () => watchlistController.toggle(root.forgetContext)
        })
        root.showMenu(items, position)
    }

    // Built on first right-click, not with every card. Eagerly instantiating a
    // ContextMenu costs ~36 KB per delegate (measured), paid by every visible
    // card for a menu most are never asked for.
    Loader {
        id: menuLoader
        active: false
        sourceComponent: ContextMenu { }
    }

    function showMenu(items, position) {
        menuLoader.active = true
        // Loader.item is typed QObject, so the loaded ContextMenu's members
        // are invisible to the linter and the missing-property reports are false.
        // qmllint disable missing-property
        menuLoader.item.entries = items
        menuLoader.item.popupAt(root, position)
        // qmllint enable missing-property
    }

    // lift the hovered card above its neighbours so the scaled-up poster
    // overlaps them instead of being drawn underneath
    z: mouse.containsMouse ? 2 : 0

    // Content is centred (not top-anchored) so the row/grid can be a little
    // taller than the poster, leaving vertical slack for the hover scale-up
    // to grow into without being clipped by the surrounding view.
    Column {
        id: content
        spacing: 6
        anchors.centerIn: parent

        // scale the whole card (poster + title) as one unit on hover so the
        // gap between them is preserved; shrink slightly on press
        transformOrigin: Item.Center
        scale: mouse.pressed ? 0.95 : (mouse.containsMouse ? 1.06 : 1.0)
        Behavior on scale { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }

        Item {
            id: cover
            width: root.coverWidth; height: root.coverHeight

            // skeleton placeholder shown until the poster is ready: a surface
            // fill with an animated shimmer sweep while the image loads
            // Filler for missing/broken posters: a surface panel with a media
            // glyph (film for movies, tv for series).
            Rectangle {
                anchors.fill: parent
                radius: 14
                color: Theme.surface
                visible: !root.posterUrl || img.status === Image.Error
                AppIcon {
                    anchors.centerIn: parent
                    glyph: root.mediaType === "series" ? Icons.liveTv : Icons.theaters
                    font.pixelSize: 44
                    color: Theme.borderStrong
                }
            }

            Rectangle {
                id: skeleton
                anchors.fill: parent
                radius: 14
                color: Theme.surface
                clip: true
                // Also covers the pre-release window of a staggered load, so
                // a delayed card shows the same skeleton as a loading one.
                visible: img.status === Image.Loading
                    || (root.posterUrl.length > 0 && !root._loadReleased)

                Rectangle {
                    id: shimmer
                    height: parent.height * 2
                    width: parent.width * 0.55
                    y: -parent.height / 2
                    rotation: 18
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.0; color: "transparent" }
                        GradientStop { position: 0.5; color: Qt.rgba(1, 1, 1, 0.07) }
                        GradientStop { position: 1.0; color: "transparent" }
                    }
                    SequentialAnimation on x {
                        loops: Animation.Infinite
                        running: img.status === Image.Loading
                        NumberAnimation {
                            from: -skeleton.width * 0.6
                            to: skeleton.width * 1.2
                            duration: 1100
                            easing.type: Easing.InOutQuad
                        }
                        PauseAnimation { duration: 350 }
                    }
                }
            }

            // The poster decoder. Hidden — the ShaderEffect below draws it
            // with true rounded-corner alpha, which stays correct over ANY
            // background (the previous bg-coloured corner-notch overlay
            // turned visible the moment the ambient glow made the background
            // non-flat). Unlike the OpacityMask this replaced further back,
            // the effect samples the Image's texture directly
            // (supportsAtlasTextures) — no per-card FBO indirection.
            Image {
                id: img
                anchors.fill: parent
                // Request/decode a poster sized for this card (in device
                // pixels, so retina stays sharp), not full-res art.
                source: root._loadReleased
                    ? Img.sized(root.posterUrl, Math.round(root.coverWidth * Screen.devicePixelRatio))
                    : ""
                sourceSize.width: Math.round(root.coverWidth * Screen.devicePixelRatio)
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: true
                visible: false
            }
            ShaderEffect {
                anchors.fill: parent
                property variant source: img
                property vector2d itemSize: Qt.vector2d(width, height)
                property real radius: 14
                // Root coords -> this cover's coords (ignoring the hover
                // scale; the feather is soft enough not to care).
                property real clipLeft: root.edgeClipLeft - (content.x + cover.x)
                property real clipRight: root.edgeClipRight - (content.x + cover.x)
                property real clipTop: root.edgeClipTop - (content.y + cover.y)
                property real clipBottom: root.edgeClipBottom - (content.y + cover.y)
                property real strengthLeft: root.edgeFadeLeftStrength
                property real strengthRight: root.edgeFadeRightStrength
                property real fadeWidth: 64
                supportsAtlasTextures: true
                vertexShader: Qt.resolvedUrl("../shaders/poster.vert.qsb")
                fragmentShader: Qt.resolvedUrl("../shaders/poster.frag.qsb")
                // fade the poster in once it has decoded
                opacity: img.status === Image.Ready ? 1.0 : 0.0
                Behavior on opacity { NumberAnimation { duration: Theme.durMed } }
            }

            // rounded white frame — only on the active (hovered) poster
            Rectangle {
                anchors.fill: parent
                radius: 14
                color: "transparent"
                border.width: 2
                border.color: "white"
                visible: mouse.containsMouse
            }

            // Resume bar across the poster's bottom edge, inside the rounding.
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.margins: 6
                height: 4
                radius: 2
                color: Qt.rgba(0, 0, 0, 0.55)
                visible: root.progressFraction > 0 && !root.watched
                Rectangle {
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: parent.width * Math.max(0, Math.min(1, root.progressFraction))
                    radius: 2
                    color: Theme.accent
                    Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
                }
            }

            Rectangle {
                anchors.top: parent.top
                anchors.right: parent.right
                anchors.margins: 8
                width: 24; height: 24; radius: 12
                color: Qt.rgba(0, 0, 0, 0.6)
                visible: root.watched
                AppIcon {
                    anchors.centerIn: parent
                    glyph: Icons.check
                    font.pixelSize: 15
                    color: Theme.positive
                }
            }

            MouseArea {
                id: mouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onClicked: (event) => {
                    if (event.button === Qt.RightButton)
                        root.openMenu(Qt.point(event.x, event.y))
                    else
                        root.clicked()
                }
                onPressAndHold: (event) => root.openMenu(Qt.point(event.x, event.y))
            }
        }

        Text {
            id: label
            width: 160
            // Reserve a constant two-line height so a wrapping (2-line) title
            // doesn't make the centred Column taller and shove the poster up.
            // Short titles top-align in this fixed box.
            height: 2 * (fontMetrics.height)
            verticalAlignment: Text.AlignTop
            opacity: root.textEdgeOpacity
            text: root.title
            // brighten dim -> full on hover (scales with the card as one unit)
            color: mouse.containsMouse ? Theme.text : Theme.textDim
            elide: Text.ElideRight
            maximumLineCount: 2
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            Behavior on color { ColorAnimation { duration: Theme.durMed } }

            FontMetrics { id: fontMetrics; font: label.font }
        }

        // Reserved by the row, not by the card: within one row some cards
        // carry a subtitle (a series names its episode) and some do not (a
        // movie has nothing to add). Sizing this per-card would leave the
        // subtitled posters sitting higher than their neighbours, because the
        // Column is centred in the card. So the whole row reserves the line or
        // none of it does.
        Text {
            id: subtitleLabel
            visible: root.showSubtitle
            opacity: root.textEdgeOpacity
            width: 160
            height: visible ? subtitleMetrics.height : 0
            text: root.subtitle
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            maximumLineCount: 1
            horizontalAlignment: Text.AlignHCenter

            FontMetrics { id: subtitleMetrics; font: subtitleLabel.font }
        }
    }
}
