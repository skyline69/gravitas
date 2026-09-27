import QtQuick
import "."

Item {
    id: root
    property bool running: false
    implicitWidth: 36
    implicitHeight: 36
    visible: opacity > 0
    opacity: running ? 1.0 : 0.0
    Behavior on opacity { NumberAnimation { duration: Theme.durFast } }

    Canvas {
        id: canvas
        anchors.fill: parent
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const cx = width / 2
            const cy = height / 2
            // Callers animate the spinner open from zero width, and the first
            // paint can land before layout has sized it; a non-positive radius
            // makes ctx.arc() throw "Incorrect argument radius".
            const r = Math.min(width, height) / 2 - 3
            if (r <= 0)
                return
            ctx.lineWidth = 3
            ctx.lineCap = "round"
            ctx.strokeStyle = Theme.accent
            ctx.beginPath()
            ctx.arc(cx, cy, r, 0, Math.PI * 1.5)
            ctx.stroke()
        }
        RotationAnimator {
            target: canvas
            from: 0
            to: 360
            duration: 900
            loops: Animation.Infinite
            running: root.running
        }
    }
}
