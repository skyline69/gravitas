import QtQuick
import "."

// Simple, clean stream-loading spinner: a faint track ring with an accent
// arc sweeping around it. Same language as AppSpinner, sized for the player.
Item {
    id: root
    width: 72
    height: 72
    property bool running: visible

    // Static track ring.
    Canvas {
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const r = Math.min(width, height) / 2 - 4
            if (r <= 0)
                return
            ctx.lineWidth = 7
            ctx.strokeStyle = Qt.rgba(1, 1, 1, 0.14)
            ctx.beginPath()
            ctx.arc(width / 2, height / 2, r, 0, Math.PI * 2)
            ctx.stroke()
        }
    }

    // Sweeping accent arc.
    Canvas {
        id: arc
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const r = Math.min(width, height) / 2 - 4
            if (r <= 0)
                return
            ctx.lineWidth = 7
            ctx.lineCap = "round"
            ctx.strokeStyle = Theme.accent
            ctx.beginPath()
            ctx.arc(width / 2, height / 2, r, 0, Math.PI * 0.6)
            ctx.stroke()
        }
        RotationAnimator {
            target: arc
            from: 0
            to: 360
            duration: 1000
            loops: Animation.Infinite
            running: root.running
        }
    }
}
