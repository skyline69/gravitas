pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "."

Slider {
    id: control
    implicitHeight: 20
    // How far ahead of `from` the media is loaded, 0..1 of the range. Drawn
    // as a lighter track under the played fill (the player's buffer bar);
    // the default 0 renders nothing, so plain sliders are untouched.
    property real bufferFraction: 0
    // [{start, end, title, kind}] in value units: the track is drawn in
    // pieces with a gap at each boundary, and the piece under the pointer
    // grows (the player's chapters). Empty -- the default -- draws one track.
    property var sections: []
    // A function from a value to the text shown over the pointer while it
    // hovers the track ("12:04 · Intro"); null shows nothing.
    property var hoverText: null
    // Never grab keyboard focus on click: a focused Slider eats Space/arrow
    // keys (Space "presses" it and commits a seek on release) instead of the
    // player page's shortcuts.
    focusPolicy: Qt.NoFocus

    // `length`, not Array.isArray: a list from Python (a QVariantList
    // property) reaches QML as a sequence that isArray rejects, so the
    // player's real sections never split the bar while a JS literal did.
    readonly property bool _split: !!control.sections && control.sections.length > 1
        && control.to > control.from
    readonly property real _gap: 3
    // Where the pointer is along the track, 0..1, or -1 when it is not over it.
    readonly property real _hoverFraction: hover.hovered
        ? Math.max(0, Math.min(1, (hover.point.position.x - control.leftPadding)
            / Math.max(1, control.availableWidth)))
        : -1
    function _fraction(value: real): real {
        return Math.max(0, Math.min(1, (value - control.from) / (control.to - control.from)))
    }

    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }

    background: Item {
        id: bar
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 8

        // One track: every slider but a sectioned timeline.
        Rectangle {
            visible: !control._split
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            height: 4
            radius: 2
            color: Qt.rgba(1, 1, 1, 0.22)
            Rectangle {
                width: Math.max(0, Math.min(1, control.bufferFraction)) * parent.width
                height: parent.height
                radius: 2
                color: Qt.rgba(1, 1, 1, 0.3)
            }
            Rectangle {
                width: control.visualPosition * parent.width
                height: parent.height
                radius: 2
                color: Theme.accent
            }
        }

        // In pieces. Each clips the same three layers the single track draws,
        // offset so the fill runs continuously across the gaps.
        Repeater {
            model: control._split ? control.sections : []
            Item {
                id: piece
                required property var modelData
                required property int index
                readonly property real startX: control._fraction(piece.modelData.start) * bar.width
                readonly property real endX: control._fraction(piece.modelData.end) * bar.width
                readonly property bool under: control._hoverFraction >= 0
                    && control._hoverFraction * bar.width >= piece.startX
                    && control._hoverFraction * bar.width < piece.endX
                x: piece.startX + (piece.index > 0 ? control._gap / 2 : 0)
                width: Math.max(0, piece.endX - piece.startX
                    - (piece.index > 0 ? control._gap / 2 : 0)
                    - (piece.index < control.sections.length - 1 ? control._gap / 2 : 0))
                anchors.verticalCenter: parent.verticalCenter
                height: piece.under || (control.pressed && piece.under) ? 7 : 4
                Behavior on height { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
                clip: true
                Rectangle {
                    anchors.fill: parent
                    radius: 1.5
                    color: Qt.rgba(1, 1, 1, 0.22)
                }
                Rectangle {
                    x: -piece.x
                    width: Math.max(0, Math.min(1, control.bufferFraction)) * bar.width
                    height: parent.height
                    color: Qt.rgba(1, 1, 1, 0.3)
                }
                Rectangle {
                    x: -piece.x
                    width: control.visualPosition * bar.width
                    height: parent.height
                    color: Theme.accent
                }
            }
        }

        // What is under the pointer: time, and the section's name when it has
        // one. Kept inside the track's width so it never runs off an edge.
        Rectangle {
            id: bubble
            readonly property string text: typeof control.hoverText === "function"
                && control._hoverFraction >= 0
                ? control.hoverText(control.from
                    + control._hoverFraction * (control.to - control.from))
                : ""
            visible: bubble.text.length > 0
            width: bubbleText.implicitWidth + 16
            height: bubbleText.implicitHeight + 8
            radius: 6
            color: Qt.rgba(0.078, 0.078, 0.078, 0.92)
            border.width: 1
            border.color: Theme.borderStrong
            y: -height - 10
            x: Math.max(0, Math.min(bar.width - width,
                control._hoverFraction * bar.width - width / 2))
            Text {
                id: bubbleText
                anchors.centerIn: parent
                text: bubble.text
                color: Theme.text
                font.pixelSize: Theme.fontSmall
            }
        }
    }

    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 14
        height: 14
        radius: 7
        color: control.pressed ? Theme.accentHover : Theme.text
        // Grows in when interacting, keeps the bar sleek at rest.
        scale: control.hovered || control.pressed ? 1.0 : 0.75
        Behavior on scale { NumberAnimation { duration: Theme.durFast; easing.type: Easing.OutCubic } }
    }
}
