import QtQuick
import QtQuick.Controls
import "."

Item {
    id: bar
    signal openDetail(string type, string id)
    signal openResults()

    implicitHeight: 36
    // Animate wider on focus.
    width: field.activeFocus ? 360 : 240
    Behavior on width { NumberAnimation { duration: Theme.durMed; easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radius
        color: Theme.surfacePress
        AppIcon {
            id: mag
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacing
            anchors.verticalCenter: parent.verticalCenter
            glyph: Icons.search
            font.pixelSize: Theme.fontBody
            color: Theme.textDim
        }
        TextField {
            id: field
            anchors.left: mag.right
            anchors.leftMargin: Theme.spacing / 2
            anchors.right: parent.right
            anchors.rightMargin: Theme.spacing
            anchors.verticalCenter: parent.verticalCenter
            placeholderText: "Search or paste an IMDB/TVDB link…"
            placeholderTextColor: Theme.textDim
            color: Theme.text
            font.pixelSize: Theme.fontBody
            selectionColor: Theme.accent
            selectedTextColor: Theme.text
            background: null
            onTextChanged: searchController.queueSearch(text)
            onAccepted: if (text.length > 0) bar.openResults()
            Keys.onEscapePressed: { text = ""; field.focus = false }
        }
    }

    // Live-preview dropdown.
    Popup {
        id: dropdown
        y: bar.height + 8
        width: bar.width
        padding: 4
        visible: field.activeFocus && (list.count > 0 || busy.running)
        closePolicy: Popup.NoAutoClose

        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surface
            border.width: 1
            border.color: Theme.borderStrong
        }

        enter: Transition {
            NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.durMed; easing.type: Easing.OutCubic }
            NumberAnimation { property: "y"; from: bar.height + 2; to: bar.height + 8; duration: Theme.durMed; easing.type: Easing.OutCubic }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.durFast }
        }

        contentItem: Column {
            spacing: 0
            AppSpinner { id: busy; running: false; visible: running; width: 24; height: 24
                anchors.horizontalCenter: parent.horizontalCenter
                Connections {
                    target: searchController
                    function onLoadingChanged(loading) { busy.running = loading }
                }
            }
            ListView {
                id: list
                width: parent.width
                height: Math.min(contentHeight, 360)
                model: searchResultsModel
                interactive: true
                clip: true
                delegate: Rectangle {
                    id: resultRow
                    width: ListView.view.width
                    height: 64
                    radius: Theme.radiusSmall
                    required property string mediaId
                    required property string type
                    required property string name
                    required property string poster
                    required property string year
                    color: rowHover.hovered ? Theme.surfaceHover : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.durFast } }
                    Row {
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 10
                        Image {
                            width: 36
                            height: 52
                            fillMode: Image.PreserveAspectCrop
                            source: resultRow.poster
                            asynchronous: true
                        }
                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                text: resultRow.name
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                                elide: Text.ElideRight
                                width: list.width - 70
                            }
                            Text {
                                text: resultRow.year
                                color: Theme.textDim
                                font.pixelSize: Theme.fontSmall
                                visible: text.length > 0
                            }
                        }
                    }
                    HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            bar.openDetail(resultRow.type, resultRow.mediaId)
                            field.text = ""
                            field.focus = false
                        }
                    }
                }
            }
        }
    }
}
