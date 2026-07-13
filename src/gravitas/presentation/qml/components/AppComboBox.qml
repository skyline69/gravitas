import QtQuick
import QtQuick.Controls
import "."

ComboBox {
    id: control
    implicitHeight: Theme.controlHeight
    font.pixelSize: Theme.fontBody

    // Don't retain the keyboard-focus ring after a mouse click; Tab only.
    focusPolicy: Qt.TabFocus

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: Theme.radius
        color: control.pressed ? Theme.surfacePress : Theme.surface
        border.width: 1
        border.color: (control.activeFocus || control.hovered) ? Theme.borderStrong : Theme.border
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
        width: control.width
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
