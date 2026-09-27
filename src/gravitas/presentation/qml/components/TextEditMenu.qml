import QtQuick
import "."

// Themed replacement for the context menu Qt attaches to text controls.
//
// Since 6.9 every TextField/TextArea carries a TextEditingContextMenu, and on
// Windows that one is *native*: a grey Win32 popup with its own icons, dropped
// into the middle of a dark themed app and reachable by none of our colours.
// So each field turns it off (`ContextMenu.menu: null`) and drops one of these
// in as a child instead.
//
// Fills its parent and answers right-clicks only, so selecting text with the
// left button behaves exactly as before -- an Item accepts no pointer events
// of its own, only what its handlers ask for.
Item {
    id: root
    anchors.fill: parent

    // The control this menu edits. Defaults to the field it was declared in.
    property Item editor: parent

    // Entries are rebuilt per open, and only the ones that would actually do
    // something are listed: a menu of greyed-out rows is noise to read past,
    // and with four entries the shape stays obvious without them.
    function openAt(position) {
        const editor = root.editor
        if (!editor)
            return
        // Never let a password field copy its contents out; Qt's own menu
        // takes the same line.
        const plain = editor.echoMode === TextInput.Normal
        let items = []
        if (editor.canUndo)
            items.push({ label: "Undo", action: () => editor.undo() })
        if (editor.canRedo)
            items.push({ label: "Redo", action: () => editor.redo() })
        if (plain && editor.selectedText.length > 0) {
            if (!editor.readOnly)
                items.push({ label: "Cut", action: () => editor.cut() })
            items.push({ label: "Copy", action: () => editor.copy() })
        }
        if (!editor.readOnly && editor.canPaste)
            items.push({ label: "Paste", action: () => editor.paste() })
        if (editor.length > 0)
            items.push({ label: "Select all", action: () => editor.selectAll() })
        if (items.length === 0)
            return
        menuLoader.active = true
        // Loader.item is typed QObject; the assertion is what lets the menu's
        // own members be checked.
        const menu = menuLoader.item as ContextMenu
        menu.entries = items
        menu.popupAt(root, position)
    }

    TapHandler {
        acceptedButtons: Qt.RightButton
        // Same reasoning as ContextMenu's own rows: the default DragThreshold
        // policy never accepts the event, so the click would also land on
        // whatever sits under the field.
        gesturePolicy: TapHandler.ReleaseWithinBounds
        onTapped: eventPoint => root.openAt(eventPoint.position)
    }

    // Built on first right-click: a ContextMenu is ~36 KB, and most fields are
    // never right-clicked at all.
    Loader {
        id: menuLoader
        active: false
        sourceComponent: ContextMenu { }
    }
}
