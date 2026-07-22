import QtQuick
import "."

// One pill housing N segments with a single highlight that slides behind the
// active one. Model is a plain string list; labels are title-cased for
// display ("movie" -> "Movie") since addon type names arrive lowercase.
Item {
    id: control
    property var model: []
    property int currentIndex: 0
    signal activated(int index)

    // A single segment has nothing to switch to: drop the pointer cursor and
    // the tap target so it reads as the label it is, not a dead button.
    readonly property bool locked: control.model.length < 2

    implicitHeight: Theme.controlHeight
    implicitWidth: row.implicitWidth + pad * 2
    readonly property int pad: 4

    // Reposition the highlight under the active segment. An imperative sync
    // (not an x binding) because Repeater.itemAt() is not notifiable — a
    // binding would not re-evaluate when items are created or the index moves.
    function _sync() {
        var item = repeater.itemAt(control.currentIndex)
        if (item) {
            highlight.x = control.pad + item.x
            highlight.width = item.width
            highlight.visible = true
        } else {
            highlight.visible = false
        }
    }
    onCurrentIndexChanged: _sync()
    onModelChanged: Qt.callLater(_sync)
    Component.onCompleted: Qt.callLater(_sync)

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.surface
        border.width: 1
        border.color: Theme.borderStrong
    }

    Rectangle {
        id: highlight
        y: control.pad
        height: control.height - control.pad * 2
        radius: Theme.radiusSmall
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.22)
        visible: false
        Behavior on x { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
        Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }
    }

    Row {
        id: row
        x: control.pad
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        onWidthChanged: control._sync()

        Repeater {
            id: repeater
            model: control.model
            delegate: Item {
                required property string modelData
                required property int index
                readonly property bool active: index === control.currentIndex
                width: label.implicitWidth + Theme.spacing * 2.5
                height: control.height - control.pad * 2

                Text {
                    id: label
                    anchors.centerIn: parent
                    text: parent.modelData.length > 0
                        ? parent.modelData.charAt(0).toUpperCase() + parent.modelData.slice(1)
                        : ""
                    color: parent.active ? Theme.text : Theme.textDim
                    font.pixelSize: Theme.fontBody
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                }
                HoverHandler {
                    enabled: !control.locked
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    enabled: !control.locked
                    // Discover's filter bar floats over the poster grid, so
                    // the exclusive grab is what stops a segment tap from
                    // also opening the poster scrolled underneath it.
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: {
                        if (index !== control.currentIndex)
                            control.activated(index)
                    }
                }
            }
        }
    }
}
