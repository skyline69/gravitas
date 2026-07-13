import QtQuick
import QtQuick.Controls
import "."

ComboBox {
    id: control
    implicitHeight: Theme.controlHeight
    font.pixelSize: Theme.fontBody

    // Size the closed field (and the popup) to the longest entry, capped at
    // sizeCap — past the cap the text elides instead of growing the control.
    property real sizeCap: 320
    property real contentNeed: 90
    TextMetrics { id: itemMetrics; font: control.font }
    // Imperative (not a binding): a binding that writes itemMetrics.text and
    // reads advanceWidth depends on its own side effect and loops.
    function _measure() {
        var longest = 0
        for (var i = 0; i < control.count; i++) {
            itemMetrics.text = control.textAt(i)
            longest = Math.max(longest, itemMetrics.advanceWidth)
        }
        // left text padding + glyph slack + caret indicator + its margin
        contentNeed = longest + Theme.spacing * 3 + Theme.fontBody + Theme.spacing * 2
    }
    onModelChanged: _measure()
    onCountChanged: _measure()
    Component.onCompleted: _measure()
    implicitWidth: Math.min(sizeCap, Math.max(90, contentNeed))

    // Don't retain the keyboard-focus ring after a mouse click; Tab only.
    focusPolicy: Qt.TabFocus

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: Theme.radius
        color: control.pressed ? Theme.surfacePress : Theme.surface
        border.width: 1
        border.color: control.activeFocus ? Theme.accent : Theme.borderStrong
        Behavior on color { ColorAnimation { duration: Theme.durFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.durFast } }
    }

    contentItem: Text {
        leftPadding: Theme.spacing * 1.5
        rightPadding: control.indicator.width + Theme.spacing
        text: control.displayText
        color: Theme.text
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: AppIcon {
        x: control.width - width - Theme.spacing
        y: control.topPadding + (control.availableHeight - height) / 2
        glyph: Icons.caretDown
        rotation: control.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: Theme.durFast } }
    }

    delegate: ItemDelegate {
        width: ListView.view ? ListView.view.width : control.width
        height: Theme.controlHeight
        highlighted: control.highlightedIndex === index
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            color: highlighted ? Theme.surfaceHover : "transparent"
        }
        contentItem: Text {
            leftPadding: Theme.spacing * 1.5
            text: control.textRole.length ? (model[control.textRole] || "")
                                          : (modelData !== undefined ? modelData : "")
            color: Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    popup: Popup {
        y: control.height + 4
        // At least as wide as the field; grows to show full entries even when
        // an explicit narrow width was forced on the control, up to sizeCap.
        width: Math.max(control.width, Math.min(control.contentNeed, control.sizeCap))
        implicitHeight: Math.min(contentItem.implicitHeight, 280)
        padding: 1

        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surface
            border.width: 1
            border.color: Theme.border
        }

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
        }
    }
}
