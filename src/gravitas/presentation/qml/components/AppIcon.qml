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
    renderType: Text.NativeRendering
}
