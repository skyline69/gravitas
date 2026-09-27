import QtQuick
import "."

// What a source list says when it has no rows and is not loading. Shared by
// the movie page's inline list and the per-episode Sources page, so the two
// never disagree about what "empty" means.
//
// Two different facts end up with an empty list. The addons answered and had
// nothing playable: that is the honest state, and there is nothing to retry.
// Or a stream addon did not answer at all (`failed`): then "no sources" would
// be a false claim about the title, and the useful thing is a retry.
Column {
    id: root
    property int count: 0
    property bool loading: false
    property bool failed: false
    signal retry()

    spacing: 8
    visible: root.count === 0 && !root.loading

    Text {
        text: root.failed
            ? "Couldn't reach your stream addon."
            : "No sources available. Add a streaming addon to see sources."
        color: Theme.textDim
        font.pixelSize: Theme.fontSmall
    }
    AppButton {
        visible: root.failed
        text: "Try again"
        ghost: true
        onClicked: root.retry()
    }
}
