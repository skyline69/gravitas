pragma ComponentBehavior: Bound
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

    // Optional picture beside each entry: a function from a model row to an
    // image URL ("" for none), e.g. the language menus' flags. Rows are read
    // as `model[index]`, so this is for array models.
    property var iconSource: null
    readonly property bool hasIcons: typeof control.iconSource === "function"
    // 4:3, the flags' own shape, at a height that sits on the text's x-height.
    readonly property int iconHeight: 14
    readonly property int iconWidth: Math.round(iconHeight * 4 / 3)
    readonly property int iconGap: Theme.spacing
    function iconAt(index: int): string {
        if (!control.hasIcons || index < 0 || !control.model || index >= control.count)
            return ""
        return control.iconSource(control.model[index]) || ""
    }

    // A list longer than this opens with a search field on top: past about a
    // dozen entries, scrolling to one is slower than typing three letters of
    // it. Automatic, so a menu gains it the day its list grows -- a genre list,
    // a long-running show's seasons -- with nothing to opt into.
    property int searchThreshold: 12
    readonly property bool searchable: control.count > control.searchThreshold
    // What has been typed, and which row of the matches the keyboard is on.
    property string _query: ""
    property int _cursor: 0

    // Case- and accent-insensitive, so "e" finds "É" and "portu" finds
    // "Portuguese" however it was typed.
    function _fold(text: string): string {
        return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    }
    // The source indices that match, best first: names that start with the
    // query, then a word inside them that does ("bra" -> "Portuguese
    // (Brazil)"), then anything containing it. Order within a group is the
    // list's own.
    readonly property var _matches: {
        const _rows = control.count
        const _source = control.model
        const query = control._fold(control._query.trim())
        const ranked = [[], [], []]
        for (let i = 0; i < control.count; i++) {
            const text = control._fold(control.textAt(i))
            if (!query || text.startsWith(query))
                ranked[0].push(i)
            else if (text.includes(" " + query) || text.includes("(" + query))
                ranked[1].push(i)
            else if (text.includes(query))
                ranked[2].push(i)
        }
        return ranked[0].concat(ranked[1], ranked[2])
    }
    on_QueryChanged: control._cursor = 0

    // Select through the consumer's own handler. Every caller here binds
    // currentIndex to a controller and moves it in onActivated, so emitting
    // is what selects; assigning from in here would break that binding. Only
    // when nothing moved it (a combo with no binding) is it set directly.
    // So a handler must act on its `index` argument: currentIndex and
    // currentValue still name the OLD entry while it runs.
    function _choose(index: int) {
        control.popup.close()
        control.activated(index)
        if (control.currentIndex !== index)
            control.currentIndex = index
    }

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
            + (control.hasIcons ? control.iconWidth + control.iconGap : 0)
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

    contentItem: Item {
        ComboIcon {
            id: fieldIcon
            x: Theme.spacing * 1.5
            anchors.verticalCenter: parent.verticalCenter
            source: control.iconAt(control.currentIndex)
        }
        Text {
            anchors.fill: parent
            leftPadding: fieldIcon.visible
                ? fieldIcon.x + fieldIcon.width + control.iconGap : Theme.spacing * 1.5
            rightPadding: control.indicator.width + Theme.spacing
            text: control.displayText
            color: Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    indicator: AppIcon {
        x: control.width - width - Theme.spacing
        y: control.topPadding + (control.availableHeight - height) / 2
        glyph: Icons.caretDown
        rotation: control.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: Theme.durFast } }
    }

    // The plain list's rows (the ComboBox's own delegate model).
    delegate: ItemDelegate {
        id: entry
        // `model` covers both kinds of model a combo box is given: a role
        // lookup on an item model, and `model.modelData` on a plain array.
        required property var model
        required property int index
        width: ListView.view ? ListView.view.width : control.width
        height: Theme.controlHeight
        highlighted: control.highlightedIndex === entry.index
        HoverHandler { cursorShape: Qt.PointingHandCursor }
        background: Rectangle {
            color: entry.highlighted ? Theme.surfaceHover : "transparent"
        }
        contentItem: EntryLabel {
            sourceIndex: entry.index
            text: control.textRole.length ? (entry.model[control.textRole] || "")
                                          : (entry.model.modelData ?? "")
        }
    }

    // The search list's rows: its model is the matches, so each row is told
    // which entry of the real list it stands for.
    Component {
        id: matchRow
        ItemDelegate {
            id: match
            required property int modelData
            required property int index
            width: ListView.view ? ListView.view.width : control.width
            height: Theme.controlHeight
            highlighted: control._cursor === match.index
            onHoveredChanged: if (match.hovered) control._cursor = match.index
            onClicked: control._choose(match.modelData)
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            background: Rectangle {
                color: match.highlighted ? Theme.surfaceHover : "transparent"
            }
            contentItem: EntryLabel {
                sourceIndex: match.modelData
                text: control.textAt(match.modelData)
            }
        }
    }

    // One entry: its picture (if the menu has them) and its text. Rows without
    // a picture ("As the file says") still line up with the ones that have one.
    component EntryLabel: Item {
        id: label
        required property int sourceIndex
        required property string text
        ComboIcon {
            id: entryIcon
            x: Theme.spacing * 1.5
            anchors.verticalCenter: parent.verticalCenter
            source: control.iconAt(label.sourceIndex)
        }
        Text {
            anchors.fill: parent
            leftPadding: Theme.spacing * 1.5
                + (control.hasIcons ? control.iconWidth + control.iconGap : 0)
            text: label.text
            color: Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    // One entry's picture. SVGs are rasterised at sourceSize, so it is set to
    // the drawn size (times the screen's scale): left alone, every flag would
    // be decoded at its native 640x480 and scaled down.
    component ComboIcon: Image {
        width: control.iconWidth
        height: control.iconHeight
        visible: source.toString().length > 0
        sourceSize: Qt.size(width * Screen.devicePixelRatio, height * Screen.devicePixelRatio)
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        smooth: true
    }

    popup: Popup {
        id: menu
        y: control.height + 4
        // At least as wide as the field; grows to show full entries even when
        // an explicit narrow width was forced on the control, up to sizeCap.
        width: Math.max(control.width, Math.min(control.contentNeed, control.sizeCap))
        implicitHeight: contentItem.implicitHeight + topPadding + bottomPadding
        padding: 1
        // The search field needs the keyboard; the plain list leaves it with
        // the ComboBox, whose arrow keys walk it.
        focus: control.searchable

        // Everything before the open animation, not after it. Typing starts
        // the moment the menu appears, and keys pressed during the 180ms
        // fade-in were lost while the field only took focus once opened. The
        // scroll likewise: centring once opened let the menu first show the
        // chosen entry at the bottom edge (where moving the cursor had put
        // it) and then jump half a list. The list's height and model do not
        // wait for the popup's layout, so it can be positioned here.
        onAboutToShow: {
            if (!control.searchable)
                return
            control._query = ""
            searchField.text = ""
            // Open on the chosen entry, as the plain list does.
            control._cursor = Math.max(0, control._matches.indexOf(control.currentIndex))
            list.positionViewAtIndex(control._cursor, ListView.Center)
            searchField.forceActiveFocus()
        }

        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.surface
            border.width: 1
            border.color: Theme.border
        }

        contentItem: Item {
            // Searching sizes the list for the WHOLE list, not the matches, so
            // the popup holds still while typing narrows it.
            // The search list keeps a little air above its first row and below
            // its last, so a highlighted row never touches the divider or the
            // popup's edge.
            readonly property int listInset: control.searchable ? 4 : 0
            readonly property real listHeight: Math.min(280, control.searchable
                ? control.count * Theme.controlHeight + listInset * 2 : plainList.contentHeight)
            implicitHeight: (control.searchable ? searchField.height + divider.height : 0)
                + listHeight

            // A header, not a box: the field is borderless and sits flush in
            // the popup, and a hairline divides it from the results. A bordered
            // input inside the bordered popup stacked two frames, and the
            // hovered first row met the field's border edge to edge.
            AppTextField {
                id: searchField
                visible: control.searchable
                anchors.left: parent.left
                anchors.right: parent.right
                height: Theme.controlHeight
                leftPadding: Theme.spacing * 1.5 + searchGlyph.width + Theme.spacing
                placeholderText: "Search"
                background: Item {}
                onTextEdited: control._query = text
                AppIcon {
                    id: searchGlyph
                    x: Theme.spacing * 1.5
                    anchors.verticalCenter: parent.verticalCenter
                    glyph: Icons.search
                    color: Theme.textDim
                }
                Keys.onDownPressed: control._cursor = Math.min(control._matches.length - 1, control._cursor + 1)
                Keys.onUpPressed: control._cursor = Math.max(0, control._cursor - 1)
                Keys.onReturnPressed: {
                    if (control._matches.length > 0)
                        control._choose(control._matches[control._cursor])
                }
                Keys.onEnterPressed: {
                    if (control._matches.length > 0)
                        control._choose(control._matches[control._cursor])
                }
            }
            Rectangle {
                id: divider
                visible: control.searchable
                anchors.top: searchField.bottom
                width: parent.width
                height: control.searchable ? 1 : 0
                color: Theme.border
            }

            // Two lists, never one switching models. The plain one shows the
            // ComboBox's own delegate model and must not name a delegate:
            // setting a view's `delegate` writes it INTO an external
            // DelegateModel, so one shared view replaced the ComboBox's rows
            // with the search rows -- every row read the first entry, and the
            // closed field went blank while the menu was open.
            ListView {
                id: plainList
                visible: !control.searchable
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.listHeight
                clip: true
                model: !control.searchable && control.popup.visible ? control.delegateModel : null
                currentIndex: control.highlightedIndex
                ScrollIndicator.vertical: ScrollIndicator {}
            }
            ListView {
                id: list
                visible: control.searchable
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.listHeight
                clip: true
                topMargin: parent.listInset
                bottomMargin: parent.listInset
                model: control.searchable ? control._matches : null
                delegate: matchRow
                currentIndex: control._cursor
                onCurrentIndexChanged: list.positionViewAtIndex(currentIndex, ListView.Contain)
                ScrollIndicator.vertical: ScrollIndicator {}
            }

            Text {
                anchors.centerIn: list
                visible: control.searchable && control._matches.length === 0
                text: "No matches"
                color: Theme.textDim
                font.pixelSize: Theme.fontSmall
            }
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
