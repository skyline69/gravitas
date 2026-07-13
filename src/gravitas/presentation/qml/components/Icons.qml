pragma Singleton
import QtQuick

QtObject {
    property FontLoader loader: FontLoader { source: "../assets/Phosphor.ttf" }
    readonly property string family: loader.name

    // Phosphor (regular) glyph codepoints, kept as ASCII escapes so the
    // source stays readable/diffable (no raw private-use glyphs in the file).
    readonly property string caretDown: String.fromCharCode(0xe136)
    readonly property string arrowLeft: String.fromCharCode(0xe058)
    readonly property string play: String.fromCharCode(0xe3d0)
    readonly property string pause: String.fromCharCode(0xe39e)
    readonly property string search: String.fromCharCode(0xe30c)
    readonly property string x: String.fromCharCode(0xe4f6)
    readonly property string gear: String.fromCharCode(0xe270)
    readonly property string trash: String.fromCharCode(0xe4a6)
}
