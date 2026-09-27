pragma ComponentBehavior: Bound
import QtQuick
import "."

// One playable source. Structured fields (resolution/instant/tags/stars)
// render as chips; free-form leftovers go on the dim lines. Falls back to a
// plain name row when the label had no recognisable structure.
//
// The row sizes itself to its content: `height` is never asserted, it is
// derived from the text stack, so no font size, locale, or badge count can
// push a chip outside the rounded rect.
Rectangle {
    id: root
    property string name
    property string subtitle
    property string resolution: ""
    property bool instant: false
    property var tags: []
    property int stars: 0
    // Named detailText, not `detail`: a property named `detail` shadows the
    // Detail page id inside delegates and breaks `detail.playUrl(...)`.
    property string detailText: ""
    // Set only when the connection sort is on and this source asks for more
    // bandwidth than was measured. Dims the row and adds a chip; never hides
    // or disables it -- the estimate can be wrong, and the user is allowed to
    // disagree with it.
    property bool overBudget: false
    // Set when the source carries more pixels than the best screen can show.
    // It is why a 4K row can sit under a 1440p one: both put 1440p on the
    // panel, and this one pays more for it. Says so rather than leaving the
    // order looking broken.
    property bool oversized: false
    // Marked as the best source for one quality level on this machine (see
    // application/recommendation.py). `reason` is the sentence behind the
    // mark, shown on hover — a badge that cannot explain itself is a brand,
    // not information.
    property bool recommended: false
    property string reason: ""
    // Clicked while the list on screen was the stored one: waiting for the
    // fresh list's link to this release (DetailController.pickWhenFresh).
    property bool pending: false
    signal clicked()

    readonly property bool structured: resolution.length > 0 || instant
        || tags.length > 0 || stars > 0 || recommended

    // Chip heights follow the font rather than a hardcoded 20/26, so the row
    // still holds together at a different font size or device pixel ratio.
    readonly property int chipHeight: Math.ceil(chipMetrics.height) + 6
    readonly property int padding: 10
    readonly property int minHeight: 64

    TextMetrics {
        id: chipMetrics
        font.pixelSize: Theme.fontSmall
        text: "Ag"
    }

    implicitHeight: Math.max(minHeight, content.implicitHeight + root.padding * 2)
    height: implicitHeight
    radius: Theme.radiusSmall
    color: mouse.containsMouse ? Theme.surfaceHover : Theme.surface
    Behavior on color { ColorAnimation { duration: Theme.durFast } }
    // The recommended rows sit at the top of the list, where "first" alone
    // does not distinguish them from "the addon listed it first". The outline
    // is what says the position was earned.
    border.width: root.recommended ? 1 : 0
    border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.55)
    // Two factors, not one Behavior on opacity: a Behavior would chase every
    // frame of the entrance below with its own 120ms lag.
    readonly property real dim: root.overBudget && !mouse.containsMouse ? 0.62 : 1
    property real _dim: dim
    Behavior on _dim { NumberAnimation { duration: Theme.durFast } }
    opacity: root._dim * root.entrance

    // Entrance: 0 is below and transparent, 1 is at rest. Rows are born at
    // rest; a list that has just been filled calls flyIn() so its rows rise
    // into place one after another rather than all appearing in one frame.
    property real entrance: 1
    transform: Translate { y: (1 - root.entrance) * 18 }
    function flyIn(delayMs: int) {
        root.entrance = 0
        entranceDelay.duration = Math.max(0, delayMs)
        entranceAnim.restart()
    }
    SequentialAnimation {
        id: entranceAnim
        PauseAnimation { id: entranceDelay }
        NumberAnimation {
            target: root
            property: "entrance"
            to: 1
            duration: 340
            easing.type: Easing.OutCubic
        }
    }

    // Resolution badge. Pinned to the row itself, so the text column beside it
    // never has to reserve a guessed amount of space for it.
    Rectangle {
        id: resBadge
        visible: root.resolution.length > 0
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        width: resText.implicitWidth + 16
        height: root.chipHeight + 6
        radius: Theme.radiusSmall
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.18)
        Text {
            id: resText
            anchors.centerIn: parent
            text: root.resolution
            color: Theme.accentHover
            font.pixelSize: Theme.fontSmall
            font.bold: true
        }
    }

    // Play affordance on hover. Laid out before the text column reads its
    // position, and always present (only faded), so the column's right edge
    // does not shift on hover.
    AppIcon {
        id: playIcon
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        glyph: Icons.play
        font.pixelSize: 22
        color: Theme.accentHover
        opacity: mouse.containsMouse && !root.pending ? 1 : 0
        scale: mouse.containsMouse ? 1 : 0.7
        Behavior on opacity { NumberAnimation { duration: Theme.durFast } }
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutBack } }
    }

    AppSpinner {
        anchors.centerIn: playIcon
        width: 22
        height: 22
        running: root.pending
    }

    Column {
        id: content
        // Anchoring both edges gives the column a definite width, which is what
        // lets the children below bind to `parent.width` without a loop.
        anchors.left: resBadge.visible ? resBadge.right : parent.left
        anchors.right: playIcon.left
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3

        // Flow, not Row: with enough tags the chips wrap onto a second line and
        // grow the row instead of running under the play icon.
        Flow {
            width: parent.width
            spacing: 6

            // Fallback: raw name when nothing structured was recognised.
            Text {
                visible: !root.structured
                width: Math.min(implicitWidth, parent.width)
                height: root.chipHeight
                verticalAlignment: Text.AlignVCenter
                text: root.name
                color: Theme.text
                font.bold: true
                font.pixelSize: Theme.fontBody
                elide: Text.ElideRight
                maximumLineCount: 1
            }

            Rectangle {
                visible: root.recommended
                width: recommendedText.implicitWidth + 16
                height: root.chipHeight
                radius: height / 2
                color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.22)
                Text {
                    id: recommendedText
                    anchors.centerIn: parent
                    text: "Recommended"
                    color: Theme.accentHover
                    font.pixelSize: Theme.fontSmall
                    font.bold: true
                }
            }
            Rectangle {
                visible: root.instant
                width: instantText.implicitWidth + 14
                height: root.chipHeight
                radius: height / 2
                color: Qt.rgba(Theme.positive.r, Theme.positive.g, Theme.positive.b, 0.16)
                Text {
                    id: instantText
                    anchors.centerIn: parent
                    text: "Instant"
                    color: Theme.positive
                    font.pixelSize: Theme.fontSmall
                }
            }
            Repeater {
                model: root.tags
                Rectangle {
                    id: tag
                    required property string modelData
                    width: tagText.implicitWidth + 14
                    height: root.chipHeight
                    radius: height / 2
                    color: "transparent"
                    border.width: 1
                    border.color: Theme.borderStrong
                    Text {
                        id: tagText
                        anchors.centerIn: parent
                        text: tag.modelData
                        color: Theme.text
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }
            Text {
                visible: root.stars > 0
                height: root.chipHeight
                verticalAlignment: Text.AlignVCenter
                text: "★".repeat(root.stars)
                color: "#f5c518"
                font.pixelSize: Theme.fontSmall
            }
            Rectangle {
                visible: root.oversized
                width: oversizedText.implicitWidth + 14
                height: root.chipHeight
                radius: height / 2
                color: "transparent"
                border.width: 1
                border.color: Theme.textDim
                Text {
                    id: oversizedText
                    anchors.centerIn: parent
                    text: "Downscaled"
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }
            }
            Rectangle {
                visible: root.overBudget
                width: budgetText.implicitWidth + 14
                height: root.chipHeight
                radius: height / 2
                color: "transparent"
                border.width: 1
                border.color: Theme.textDim
                Text {
                    id: budgetText
                    anchors.centerIn: parent
                    text: "May buffer"
                    color: Theme.textDim
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        // Leftover free-form text (addon name, size, codec…), then the
        // title line when it genuinely differs from the name.
        Text {
            visible: text.length > 0 && root.structured
            width: parent.width
            text: root.detailText
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            maximumLineCount: 1
            textFormat: Text.PlainText
        }
        Text {
            visible: text.length > 0
            width: parent.width
            text: root.subtitle
            color: Theme.textDim
            font.pixelSize: Theme.fontSmall
            elide: Text.ElideRight
            maximumLineCount: 1
            textFormat: Text.PlainText
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
        onContainsMouseChanged: {
            if (root.reason.length === 0)
                return
            if (containsMouse)
                reasonTimer.restart()
            else {
                reasonTimer.stop()
                reasonTip.close()
            }
        }
    }

    Timer { id: reasonTimer; interval: 400; onTriggered: reasonTip.open() }
    AppToolTip {
        id: reasonTip
        text: root.reason
        x: 12
        y: root.height + 4
    }
}
