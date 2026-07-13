pragma Singleton
import QtQuick

// Mouse-wheel scroll helper. Flicks the view (so it's smooth and the auto-hiding
// scrollbar appears) with a base velocity that RAMPS UP when you scroll quickly:
// successive notches within `windowMs` multiply the speed up to `maxAccel`, and
// a pause resets to the base. Used from a NoButton MouseArea over the view.
QtObject {
    property int baseVelocity: 1400 // px/s per notch at rest
    property real maxAccel: 2.5 // ramp ceiling for rapid scrolling
    property real accelStep: 0.3 // added per fast notch
    property int windowMs: 130 // notches within this window accelerate

    property real _accel: 1.0
    property double _lastT: 0

    function wheel(view, w) {
        var now = Date.now()
        if (now - _lastT < windowMs)
            _accel = Math.min(_accel + accelStep, maxAccel)
        else
            _accel = 1.0
        _lastT = now
        var dir = w.angleDelta.y > 0 ? 1 : -1
        view.flick(0, dir * baseVelocity * _accel)
    }
}
