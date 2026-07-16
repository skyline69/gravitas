"""Instantiate QML components and fail on any warning they emit.

tests/test_composition.py proves Main.qml *loads*; it cannot prove loading was
clean, because QML reports most mistakes as runtime warnings and still hands
back a valid object. A silent warning has already cost us one real bug (a Popup
reaching for Window.window, which only attaches to Items, so every guarded
branch quietly took its fallback).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent, QQmlEngine
from pytest import MonkeyPatch

from gravitas.main import _QML_DIR, build_app

_COMPONENTS_DIR = _QML_DIR / "components"


@pytest.fixture
def app_font(qapp: QGuiApplication) -> None:
    """Give the test process the application font build_app installs.

    Without it the Basic style asks for its default family, "Sans Serif", which
    does not exist on macOS: Qt then spends ~120ms populating font aliases and
    warns about the cost -- a warning this test rightly refuses to ignore, but
    one the real app never emits because it runs with Inter. Same reason the
    style is pinned in conftest: what is measured here has to be what ships.
    """
    font_id = QFontDatabase.addApplicationFont(str(_QML_DIR / "assets" / "Inter.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        qapp.setFont(QFont(families[0]))


@pytest.fixture
def qml_warnings(qapp: object, app_font: None) -> Iterator[list[str]]:
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

    The style is not set here -- QQuickStyle.setStyle() only takes effect before
    the first Controls import in the process, so calling it from a test is
    already too late and warns in turn. conftest pins QT_QUICK_CONTROLS_STYLE
    instead, which Qt reads first. Style very much does affect what this
    measures: under a native style every customized App* component warns.
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


def test_settings_page_survives_its_controllers_going_away(
    qml_warnings: list[str], tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A QML binding that dereferences a context property without checking the
    object itself throws once that object goes away.

    Context properties go null at engine teardown, and every live binding
    re-evaluates on the way down — so `settingsController.subColor` (rather
    than `settingsController && settingsController.subColor`) fills the console
    with TypeErrors every time the app exits. Nothing is broken by then, but
    the noise buries real errors, which is exactly how the Window.window bug
    hid.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        component = QQmlComponent(engine, str(_QML_DIR / "Settings.qml"))
        page = component.create(engine.rootContext())
        assert page is not None, component.errorString()
        qml_warnings.clear()  # ignore anything from bringing the app up
    finally:
        del engine
    assert qml_warnings == []


def test_poster_card_builds_its_menu_only_when_asked(qml_warnings: list[str]) -> None:
    """A ContextMenu costs ~36 KB per delegate if built eagerly (measured), paid
    by every visible card for a menu most are never asked for. It must stay
    behind a Loader -- and must still open."""
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 800; height: 600
            function loader() {
                for (var i = 0; i < card.children.length; i++)
                    if (card.children[i].toString().indexOf("QQuickLoader") >= 0)
                        return card.children[i]
                return null
            }
            PosterCard {
                id: card
                title: "A Show"
                mediaType: "series"
                progressFraction: 0.5
                forgetContext: ({ mediaId: "tt9", videoId: "", type: "series",
                                  name: "A Show", poster: "", label: "" })
            }
            Component.onCompleted: {
                var l = loader()
                if (l === null) throw new Error("no Loader: the menu is eager again")
                if (l.active || l.item !== null)
                    throw new Error("menu was built before anyone right-clicked")
                card.openMenu(Qt.point(10, 10))
                if (l.item === null) throw new Error("menu did not build on demand")
                if (!l.item.visible) throw new Error("menu built but never shown")
                if (l.item.entries.length !== 2)
                    throw new Error("expected mark-watched + forget, got "
                                    + l.item.entries.length)
            }
        }
        """
    )
    assert qml_warnings == []


def test_addon_install_dialog_instantiates_without_warnings(qml_warnings: list[str]) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            AddonInstallDialog { id: dialog }
            Component.onCompleted: dialog.ask({
                name: "PTube",
                version: "1.0.0",
                host: "ptube.ers.pw",
                description: "Streams things.",
                logo: "",
                adult: true,
                p2p: true,
                configurationRequired: false,
                configureUrl: "https://ptube.ers.pw/configure"
            })
        }
        """
    )
    assert qml_warnings == []


def test_addon_install_dialog_configuration_required_variant(qml_warnings: list[str]) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            AddonInstallDialog { id: dialog }
            Component.onCompleted: dialog.ask({
                name: "Needs Setup",
                version: "2.0",
                host: "addon.example",
                description: "",
                logo: "",
                adult: false,
                p2p: false,
                configurationRequired: true,
                configureUrl: "https://addon.example/configure"
            })
        }
        """
    )
    assert qml_warnings == []
