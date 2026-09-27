import QtQuick
import "."

// One of the player's format marks, bare on the title bar's shade, with what
// it means on hover. A format with a mark of its own shows it (the white logos under qml/formats/, see
// scripts/vendor_format_logos.py); resolutions and surround channels show the
// icon font's glyph; HLG, which has no mark, is lettered in the style of the
// HDR10 one.
Item {
    id: root
    // PlayerController.mediaBadges' `format`, `label` and `detail`.
    property string format
    property string label
    property string detail: ""

    // Height of each logo inside the badge: the marks differ in proportion,
    // and a common height makes the stacked HDR10 frame look too heavy
    // beside Dolby's single line.
    readonly property var logos: ({
        "dolby-vision": { file: "dolby-vision.svg", height: 13 },
        "dolby-atmos": { file: "dolby-atmos.svg", height: 13 },
        "hdr10": { file: "hdr10.svg", height: 18 },
        "hdr10-plus": { file: "hdr10-plus.svg", height: 18 },
        "dts-x": { file: "dts-x.png", height: 15 }
    })
    readonly property var logo: logos[format] ?? null
    readonly property string glyph: format === "4k" ? Icons.fourK
        : format === "channels" ? Icons.surround
        : ["1440p", "1080p", "720p"].indexOf(format) >= 0 ? Icons.hd
        : ""

    // Every mark is centred on this height, so a row of them shares one
    // midline whatever each one's own height.
    implicitWidth: content.implicitWidth
    implicitHeight: 24

    Row {
        id: content
        anchors.centerIn: parent
        spacing: 4

        Image {
            visible: root.logo !== null
            anchors.verticalCenter: parent.verticalCenter
            source: root.logo ? "../formats/" + root.logo.file : ""
            height: root.logo ? root.logo.height : 0
            // Rendered at twice the size it is shown, so the SVG stays sharp
            // on a 2x display.
            sourceSize.height: height * 2
            fillMode: Image.PreserveAspectFit
            mipmap: true
            smooth: true
        }

        // The glyph's ink, not its line: Material's glyphs sit high in the
        // font's ascent-to-descent box, so centring the Text left the 4K and
        // surround marks above the logos and text beside them. The item is
        // sized to the glyph's tight bounds and the Text placed so that ink
        // fills it exactly.
        Item {
            visible: root.glyph.length > 0
            anchors.verticalCenter: parent.verticalCenter
            width: ink.tightBoundingRect.width
            height: ink.tightBoundingRect.height
            TextMetrics {
                id: ink
                font: icon.font
                text: root.glyph
            }
            AppIcon {
                id: icon
                glyph: root.glyph
                // Material pads its glyphs: these sizes put the 4K and HD
                // boxes at the height of the logos beside them.
                font.pixelSize: root.format === "channels" ? 20 : 30
                color: Theme.text
                verticalAlignment: Text.AlignTop
                horizontalAlignment: Text.AlignLeft
                x: -ink.tightBoundingRect.x
                y: -(icon.baselineOffset + ink.tightBoundingRect.y)
            }
        }

        // The channel count beside its glyph, and HLG lettered in a frame.
        Rectangle {
            visible: root.format === "channels" || root.format === "hlg"
            anchors.verticalCenter: parent.verticalCenter
            width: text.implicitWidth + (framed ? 10 : 0)
            height: text.implicitHeight + (framed ? 2 : 0)
            readonly property bool framed: root.format === "hlg"
            radius: 3
            color: "transparent"
            border.width: framed ? 1.2 : 0
            border.color: Theme.text
            TextMetrics {
                id: letters
                font: text.font
                text: root.label
            }
            Text {
                id: text
                anchors.horizontalCenter: parent.horizontalCenter
                // Centred on its ink, like the glyph beside it: digits and
                // capitals have no descenders, and the line box's room for
                // them put "5.1" below the surround mark.
                y: (parent.height - letters.tightBoundingRect.height) / 2
                    - (text.baselineOffset + letters.tightBoundingRect.y)
                text: root.label
                color: Theme.text
                font.bold: true
                font.letterSpacing: 0.4
                // Lettered to sit beside the HDR10 mark: a little smaller
                // than the badge text elsewhere.
                font.pixelSize: root.format === "hlg" ? 11 : Theme.fontSmall
            }
        }
    }

    HoverHandler {
        onHoveredChanged: {
            if (hovered && root.detail.length > 0) {
                tipTimer.restart()
            } else {
                tipTimer.stop()
                tip.close()
            }
        }
    }
    Timer { id: tipTimer; interval: 500; onTriggered: tip.open() }
    AppToolTip {
        id: tip
        text: root.detail
        // Against root, not `parent`: see BackButton.
        x: root.width - width
        y: root.height + 8
    }
}
