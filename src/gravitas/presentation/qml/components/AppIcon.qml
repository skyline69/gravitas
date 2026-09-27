import QtQuick
import "."

Text {
    id: root
    property string glyph
    text: root.glyph
    font.family: Icons.family
    font.pixelSize: Theme.fontTitle
    color: Theme.textDim
    verticalAlignment: Text.AlignVCenter
    horizontalAlignment: Text.AlignHCenter
    // QtRendering (distance-field) stays crisp under scale transforms and
    // animation; NativeRendering blurs badly when the glyph is scaled (e.g.
    // the button press-shrink).
    renderType: Text.QtRendering
}
