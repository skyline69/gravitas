pragma Singleton
import QtQuick

QtObject {
    property FontLoader loader: FontLoader { source: "../assets/MaterialSymbols.ttf" }
    readonly property string family: loader.name

    // Material Symbols Rounded (filled) glyph codepoints, kept as ASCII escapes
    // so the source stays readable/diffable (no raw private-use glyphs in the
    // file). The bundled font is a static FILL=1 instance subset to just these.
    readonly property string caretDown: String.fromCharCode(0xe313) // keyboard_arrow_down
    readonly property string arrowLeft: String.fromCharCode(0xe5c4) // arrow_back
    readonly property string play: String.fromCharCode(0xe037) // play_arrow
    readonly property string pause: String.fromCharCode(0xe034) // pause
    readonly property string search: String.fromCharCode(0xe8b6) // search
    readonly property string x: String.fromCharCode(0xe14c) // close
    readonly property string gear: String.fromCharCode(0xe8b8) // settings
    readonly property string trash: String.fromCharCode(0xe872) // delete
    readonly property string dashboard: String.fromCharCode(0xe871) // dashboard
    readonly property string theaters: String.fromCharCode(0xe54d) // theaters
    readonly property string liveTv: String.fromCharCode(0xe639) // live_tv
    readonly property string fire: String.fromCharCode(0xea05) // local_fire_department
}
