import QtQuick
import "."

// The entrance fade a page runs on itself.
//
// StackView's own pushEnter/popEnter transitions would do this, but StackView
// drops EVERY input event for as long as a transition runs (`busy`): press
// Back and click a row inside the next ~180ms and the click lands nowhere.
// Measured -- taps are swallowed for the whole transition and start landing
// the frame `busy` clears. So Main leaves StackView's four transitions empty
// (nothing to run, `busy` never latches) and each page fades itself in:
//
//     Item {
//         id: page
//         opacity: 0
//         PageFade { id: pageFade; target: page }
//         Component.onCompleted: pageFade.restart()   // pushed
//         StackView.onActivating: pageFade.restart()  // popped back to
//     }
//
// The page being left drops out at once instead of cross-fading -- the cost of
// having the stack stay responsive through the animation.
NumberAnimation {
    property: "opacity"
    from: 0
    to: 1
    duration: Theme.durMed
    easing.type: Easing.OutCubic
}
