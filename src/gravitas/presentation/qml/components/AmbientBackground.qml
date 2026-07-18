import QtQuick

// Ambient page background: two barely-there glows drifting over Theme.bg.
// One fullscreen quad with a trivial fragment shader — the cheapest possible
// animated background — but still a repaint per frame, so it runs only while
// it can be seen: paused when the app is inactive and hidden (by Main) under
// the player. If the shader cannot compile on some exotic backend the effect
// simply renders nothing and the window's solid colour shows — never worse
// than before.
ShaderEffect {
    id: root

    property real time: 0
    readonly property real aspect: width / Math.max(1, height)

    // 0 → 200π over 4 minutes: glow orbits take ~50-80s, the breathing
    // pulses ~10-15s — visibly alive without demanding attention. Every
    // angular rate inside the shader is a multiple of 0.01, so the whole
    // range is full cycles for each of them and the loop wraps without a
    // visible jump.
    NumberAnimation on time {
        from: 0
        to: 628.3185307179587
        duration: 240000
        loops: Animation.Infinite
        running: root.visible && Qt.application.state === Qt.ApplicationActive
    }

    // Qt.resolvedUrl, not a bare relative string: the bare form resolves
    // against the instantiating document, not this file.
    fragmentShader: Qt.resolvedUrl("../shaders/background.frag.qsb")
}
