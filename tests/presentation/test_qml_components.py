"""Instantiate QML components and fail on any warning they emit.

tests/test_composition.py proves Main.qml *loads*; it cannot prove loading was
clean, because QML reports most mistakes as runtime warnings and still hands
back a valid object. A silent warning has already cost us one real bug (a Popup
reaching for Window.window, which only attaches to Items, so every guarded
branch quietly took its fallback).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtQml import QQmlComponent, QQmlEngine

from gravitas.main import _QML_DIR

_COMPONENTS_DIR = _QML_DIR / "components"


@pytest.fixture
def qml_warnings(qapp: object) -> Iterator[list[str]]:
    """Collect Qt warnings emitted while a component is instantiated."""
    collected: list[str] = []

    def handler(mode: QtMsgType, _context: object, message: str) -> None:
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            collected.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        yield collected
    finally:
        qInstallMessageHandler(previous)


def _instantiate(source: str) -> None:
    """Build `source` as QML inside a real Window, then tear it down.

    The Quick Controls style is left alone: QQuickStyle.setStyle() only takes
    effect before the first Controls import in the process, so setting it here
    would itself warn once another test has loaded QML. Style does not affect
    what this test measures.
    """
    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(source.encode("utf-8"), _COMPONENTS_DIR.as_uri() + "/probe.qml")
    assert not component.isError(), component.errorString()
    obj = component.create()
    assert obj is not None, component.errorString()
    obj.deleteLater()


def test_confirm_dialog_instantiates_without_warnings(qml_warnings: list[str]) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            ConfirmDialog { id: dialog; heading: "Forget?"; body: "Gone." }
            Component.onCompleted: dialog.ask()
        }
        """
    )
    assert qml_warnings == []


def test_context_menu_instantiates_and_opens_without_warnings(
    qml_warnings: list[str],
) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            Item { id: host; anchors.fill: parent }
            ContextMenu { id: menu }
            Component.onCompleted: {
                menu.entries = [{ label: "Forget progress", action: function() {} }]
                menu.popupAt(host, Qt.point(40, 50))
            }
        }
        """
    )
    assert qml_warnings == []
