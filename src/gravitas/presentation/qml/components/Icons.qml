pragma Singleton
import QtQuick

QtObject {
    property FontLoader loader: FontLoader { source: "../assets/MaterialSymbols.ttf" }
    readonly property string family: loader.name

    // Material Icons Round (classic, filled) glyph codepoints, kept as ASCII
    // escapes so the source stays readable/diffable (no raw private-use glyphs
    // in the file). The bundled font is subset to just these; regenerate with
    // scripts/subset_icons.py (keep its GLYPHS list in sync — it prints the
    // codepoints to use here).
    readonly property string caretDown: String.fromCharCode(0xe313) // keyboard_arrow_down
    readonly property string arrowLeft: String.fromCharCode(0xe5c4) // arrow_back
    readonly property string play: String.fromCharCode(0xe037) // play_arrow
    readonly property string pause: String.fromCharCode(0xe034) // pause
    readonly property string search: String.fromCharCode(0xe8b6) // search
    readonly property string x: String.fromCharCode(0xe5cd) // close
    readonly property string gear: String.fromCharCode(0xe8b8) // settings
    readonly property string trash: String.fromCharCode(0xe872) // delete
    readonly property string dashboard: String.fromCharCode(0xe871) // dashboard
    readonly property string theaters: String.fromCharCode(0xe8da) // theaters
    readonly property string liveTv: String.fromCharCode(0xe639) // live_tv
    readonly property string fire: String.fromCharCode(0xef55) // local_fire_department
    // player controls
    readonly property string volumeUp: String.fromCharCode(0xe050) // volume_up
    readonly property string volumeOff: String.fromCharCode(0xe04f) // volume_off
    readonly property string subtitles: String.fromCharCode(0xe048) // subtitles
    readonly property string audio: String.fromCharCode(0xe1b8) // graphic_eq
    readonly property string fullscreen: String.fromCharCode(0xe5d0) // fullscreen
    readonly property string fullscreenExit: String.fromCharCode(0xe5d1) // fullscreen_exit
    readonly property string replay10: String.fromCharCode(0xe059) // replay_10
    readonly property string forward10: String.fromCharCode(0xe056) // forward_10
}
