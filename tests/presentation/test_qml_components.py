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


# Emitted by the offscreen platform plugin on Windows, which uses Qt's own
# font database and looks for the font directory the wheel does not ship. It
# says nothing about the component under test -- the real app runs on the
# `windows` plugin, which reads the system fonts and never warns.
_ENVIRONMENT_WARNINGS = ("QFontDatabase: Cannot find font directory",)


@pytest.fixture
def qml_warnings(qapp: object, app_font: None) -> Iterator[list[str]]:
    """Collect Qt warnings emitted while a component is instantiated."""
    collected: list[str] = []

    def handler(mode: QtMsgType, _context: object, message: str) -> None:
        if any(noise in message for noise in _ENVIRONMENT_WARNINGS):
            return
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            collected.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        yield collected
    finally:
        qInstallMessageHandler(previous)


def _instantiate(source: str, context: dict[str, object] | None = None) -> None:
    """Build `source` as QML inside a real Window, then tear it down.

    The style is not set here -- QQuickStyle.setStyle() only takes effect before
    the first Controls import in the process, so calling it from a test is
    already too late and warns in turn. conftest pins QT_QUICK_CONTROLS_STYLE
    instead, which Qt reads first. Style very much does affect what this
    measures: under a native style every customized App* component warns.
    """
    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    for name, obj in (context or {}).items():
        engine.rootContext().setContextProperty(name, obj)
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


def test_context_menu_click_does_not_leak_beneath_the_popup(qapp: object) -> None:
    """The entry TapHandler must take the exclusive grab: with the default
    passive policy the same click also reached items under the modal popup
    (picking "Mark as watched" on an episode row simultaneously clicked the
    row and pushed the Sources page underneath the menu)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    engine = QQmlEngine()
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import QtQuick.Window
        import "."

        Window {
            id: win
            width: 400; height: 400; visible: true
            property int underTaps: 0
            property int actions: 0
            // Live bindings, not a snapshot: popupAt clamps against the
            // overlay, whose size settles only once the window is exposed.
            property real menuX: menu.x
            property real menuY: menu.y
            Item {
                id: host
                anchors.fill: parent
                TapHandler { onTapped: win.underTaps++ }
            }
            ContextMenu { id: menu }
            Component.onCompleted: {
                menu.entries = [{ label: "Mark as watched", action: function() { win.actions++ } }]
                menu.popupAt(host, Qt.point(120, 120))
            }
        }
        """,
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    assert not component.isError(), component.errorString()
    win = component.create()
    assert win is not None, component.errorString()
    QTest.qWait(300)  # window exposure + the popup's enter transition
    # Click the middle of the first (only) entry row: padding 4 + half the
    # control height, safely inside the popup.
    x = int(win.property("menuX")) + 30
    y = int(win.property("menuY")) + 4 + 18
    QTest.mouseClick(win, Qt.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    QTest.qWait(100)
    assert win.property("actions") == 1, "the menu entry itself must fire"
    assert win.property("underTaps") == 0, "the click leaked through the popup"
    win.deleteLater()


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


async def test_detail_page_hides_the_previous_films_ratings(
    qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Open a film, go back, open another: the second page must not wear the
    first one's Rotten Tomatoes / Letterboxd pills.

    DetailController.load is an asyncSlot, so QML's call returns a loop turn
    before the reset inside it runs -- and StackView builds the new Detail
    page in that window, while the controller still holds the previous item's
    ratings. Binding straight to them put the old pill on screen at once (no
    animation: a Behavior does not run on a binding's first evaluation), which
    then faded out through a bare "%" as the reset landed. `ratingsReady`
    compares ids so the pills only ever animate in with their own numbers.

    The controller's use cases are swapped for fakes rather than built anew:
    it has to be the one QML is actually bound to, and it must not reach the
    network.
    """
    from gravitas.domain.models import Ratings

    from .test_detail_controller import (
        FakeGetDetail,
        FakeGetRatings,
        FakeResolve,
        _drain_pending_tasks,
    )

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = engine.rootContext().contextProperty("detailController")
        controller._get_detail = FakeGetDetail()
        controller._resolve_stream = FakeResolve()
        controller._get_ratings = FakeGetRatings(Ratings(rotten_tomatoes="87", letterboxd="4.1"))

        await controller.load("movie", "tt_A")
        await _drain_pending_tasks()
        assert controller.ratingsFor == "tt_A", "the first film's ratings are loaded"

        component = QQmlComponent(engine, str(_QML_DIR / "Detail.qml"))
        page = component.createWithInitialProperties(
            {"mediaType": "movie", "mediaId": "tt_B"}, engine.rootContext()
        )
        assert page is not None, component.errorString()

        assert page.property("ratingsReady") is False, (
            "the new page is bound to the previous film's ratings"
        )

        await _drain_pending_tasks()
        await _drain_pending_tasks()
        assert controller.ratingsFor == "tt_B"
        assert page.property("ratingsReady") is True, "its own ratings must show"
        page.deleteLater()
    finally:
        del engine


def test_poster_card_builds_its_menu_only_when_asked(qml_warnings: list[str]) -> None:
    """A ContextMenu costs ~36 KB per delegate if built eagerly (measured), paid
    by every visible card for a menu most are never asked for. It must stay
    behind a Loader -- and must still open."""
    from PySide6.QtCore import QObject, Slot

    class _StubWatchlist(QObject):
        # openMenu asks membership at open time to label the watchlist entry.
        @Slot(str, result=bool)
        def contains(self, media_id: str) -> bool:
            return False

    stub = _StubWatchlist()
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
                if (l.item.entries.length !== 3)
                    throw new Error("expected mark-watched + forget + watchlist, got "
                                    + l.item.entries.length)
                if (l.item.entries[2].label !== "Add to watchlist")
                    throw new Error("watchlist entry mislabelled: " + l.item.entries[2].label)
            }
        }
        """,
        context={"watchlistController": stub},
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


def test_app_checkbox_instantiates_without_warnings(qml_warnings: list[str]) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            AppCheckBox { checked: true; label: "Sync forgets to Trakt" }
        }
        """
    )
    assert qml_warnings == []


def test_app_checkbox_reports_the_requested_value(qapp: object) -> None:
    """Stateless by design: a click reports the flipped value through
    toggled() and leaves `checked` alone — the backend binding is the only
    thing allowed to move the visual."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import QtQuick.Window
        import "."

        Window {
            id: win
            width: 400; height: 100; visible: true
            property var reported: []
            AppCheckBox {
                x: 0; y: 38
                checked: false
                label: "Probe"
                onToggled: (value) => win.reported.push(value)
            }
        }
        """,
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    assert not component.isError(), component.errorString()
    win = component.create()
    assert win is not None, component.errorString()
    try:
        QTest.mouseClick(win, Qt.MouseButton.LeftButton, pos=QPoint(9, 50))  # type: ignore[arg-type]
        assert win.property("reported").toVariant() == [True]
    finally:
        win.deleteLater()


def test_catalog_row_strip_instantiates_without_warnings(qml_warnings: list[str]) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            CatalogRowStrip {
                width: 1200
                title: "Recommended Movies"
                addonId: ""
                type: "movie"
                catalogId: ""
                posters: null
            }
        }
        """
    )
    assert qml_warnings == []


def test_stream_row_grows_with_its_content(qapp: object) -> None:
    """The row must size itself to its badges, not to a hardcoded height.

    It used to be `height: 64` against a 62px content stack -- two pixels of
    slack, and the content was verticalCenter-anchored, so the moment a larger
    font or one more chip row pushed it past 64 the badges escaped through
    *both* edges and drew on top of the neighbouring rows.
    """
    from PySide6.QtTest import QTest

    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import QtQuick.Window
        import "."

        Window {
            width: 900; height: 400; visible: true
            property real plainHeight: plain.height
            property real plainImplicit: plain.implicitHeight
            property real chippedHeight: chipped.height
            property real chippedImplicit: chipped.implicitHeight

            StreamRow {
                id: plain
                width: 900
                name: "Some.Release.2026.1080p"
                subtitle: "a single dim line"
            }
            StreamRow {
                id: chipped
                y: 200
                width: 900
                name: "Obsession"
                subtitle: "Obsession (2026) HEVC DV HDR10 Atmos TrueHD 7.1"
                detailText: "\\u27e8Remux\\u27e9"
                resolution: "4K"
                instant: true
                stars: 5
                tags: ["Bluray", "HDR10", "DV", "Atmos", "TrueHD", "Remux",
                       "EN", "DE", "FR", "ES", "IT", "NL", "PL", "PT"]
            }
        }
        """,
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    assert not component.isError(), component.errorString()
    win = component.create()
    assert win is not None, component.errorString()
    QTest.qWait(200)  # window exposure, so the positioners settle

    plain_h = win.property("plainHeight")
    chipped_h = win.property("chippedHeight")
    # Nothing may be taller than the rect that is supposed to contain it.
    assert plain_h >= win.property("plainImplicit")
    assert chipped_h >= win.property("chippedImplicit")
    # A plain row keeps the established 64px look...
    assert plain_h == 64
    # ...while chips that wrap onto a second line grow the row instead of
    # spilling out of it.
    assert chipped_h > plain_h, f"row did not grow for wrapped chips: {chipped_h} <= {plain_h}"
    win.deleteLater()


def test_a_recycled_card_is_hidden_while_pooled_and_shown_when_handed_back(
    qapp: object, app_font: None
) -> None:
    """The blank-page bug, at delegate level.

    A view keeps recycled delegates around after their rows are gone; Qt does
    not hide them, so a card released by a removal keeps painting where it sat.
    Hiding it in onPooled and unhiding it in onReused is two imperative writes
    to one property, and tab switches fire both faster than frames arrive:
    settle on the wrong one and the card stays invisible for good, leaving an
    empty page over a model that still holds the rows. Visibility is derived
    from the index instead -- -1 while pooled -- which cannot land out of order.

    The model here shrinks (pooling delegates) and grows again (handing them
    back), while a timer watches every delegate on every frame.
    """
    from PySide6.QtTest import QTest

    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import "."

        Window {
            width: 900; height: 400
            visible: true
            property int rows: 12
            // Highest number of delegates ever caught disagreeing with their
            // own index -- sampled every frame, so a single bad frame counts.
            property int worstMismatch: 0
            ListView {
                id: list
                anchors.fill: parent
                orientation: ListView.Horizontal
                reuseItems: true
                model: parent.rows
                delegate: PosterCard {
                    required property int index
                    title: "card " + index
                    posterUrl: ""
                }
            }
            Timer {
                interval: 16; running: true; repeat: true
                onTriggered: {
                    var bad = 0
                    var kids = list.contentItem.children
                    for (var i = 0; i < kids.length; i++) {
                        var item = kids[i]
                        if (item.viewIndex === undefined)
                            continue
                        if ((item.viewIndex >= 0) !== item.visible)
                            bad++
                    }
                    if (bad > worstMismatch)
                        worstMismatch = bad
                }
            }
        }
        """,
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    assert not component.isError(), component.errorString()
    win = component.create()
    assert win is not None, component.errorString()
    QTest.qWait(200)

    # Shrink and grow repeatedly: every shrink pools delegates, every grow
    # hands them back, and the switches land faster than the view repaints.
    for rows in (2, 12, 1, 12, 3, 12, 2, 12):
        win.setProperty("rows", rows)
        QTest.qWait(40)
    QTest.qWait(200)

    assert win.property("worstMismatch") == 0, (
        "a delegate's visibility disagreed with its index: a pooled card was "
        "still painting, or an attached one stayed hidden"
    )
    win.deleteLater()


def test_text_field_replaces_qts_native_editing_menu(qml_warnings: list[str]) -> None:
    """Qt 6.9+ attaches a TextEditingContextMenu to every TextField, and on
    Windows it is a *native* menu -- a grey Win32 popup in a dark themed app,
    with no way to style it. Every field must turn it off and use ours.

    The check runs inside the QML: `ContextMenu.menu` is an attached property,
    readable only in the scope of the object it is attached to. console.warn
    lands in the collected warnings, so a regression fails this test.
    """
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import QtQuick.Controls as QQC
        import "."

        ApplicationWindow {
            width: 800; height: 200
            AppTextField {
                id: field
                anchors.centerIn: parent
                width: 400
                text: "https://v3-cinemeta.strem.io/manifest.json"
                Component.onCompleted: {
                    if (QQC.ContextMenu.menu !== null)
                        console.warn("Qt's native editing menu is still attached")
                    // Open ours the way the right-click handler does, so its
                    // entry building is exercised too.
                    for (var i = 0; i < field.children.length; i++) {
                        if (field.children[i].openAt) {
                            field.children[i].openAt(Qt.point(8, 8))
                            return
                        }
                    }
                    console.warn("no TextEditMenu on the field")
                }
            }
        }
        """
    )
    assert qml_warnings == []
