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
from PySide6.QtQuick import QQuickItem
from pytest import MonkeyPatch

from gravitas.main import _QML_DIR, build_app
from gravitas.presentation import qml_module

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
    if context:
        qml_module.bind(engine, context)
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
        _drain_until,
    )

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = qml_module.bound(engine)["DetailController"]
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

        await _drain_until(lambda: controller.ratingsFor == "tt_B")
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
                // Outside a view, so nothing hands over the row index.
                index: 0
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
        context={"WatchlistController": stub},
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


async def test_sources_page_renders_its_recommended_section_without_warnings(
    qml_warnings: list[str], tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The Sources list grows a section header once rows are marked.

    Worth a real page rather than a probe: `section.property` is read off the
    model's role names, the section delegate declares `required property
    string section`, and a mismatch in either is a runtime warning with an
    empty heading, not a load failure.
    """
    from gravitas.domain.models import MetaDetail, Stream

    class _Meta:
        async def __call__(self, type: str, item_id: str) -> MetaDetail:
            return MetaDetail(
                id=item_id,
                type="movie",
                name="Film",
                description="d",
                poster="p",
                background="b",
                videos=(),
                runtime="102 min",
            )

    class _Streams:
        async def __call__(self, type: str, item_id: str) -> list[Stream]:
            return [
                Stream(
                    name=name,
                    title="",
                    url="http://h/v.mkv",
                    info_hash=None,
                    file_idx=None,
                )
                for name in (
                    "4K ⚡ ⟨Web-dl⟩ · 24 Mbps",
                    "1080p ⟨Web-dl⟩ · 8 Mbps",
                    "1080p ⟨Web-dl⟩ · 7 Mbps",
                )
            ]

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = qml_module.bound(engine)["DetailController"]
        controller._get_detail = _Meta()
        controller._resolve_stream = _Streams()
        # The panel decides which tier a source lands in, and an offscreen Qt
        # reports whatever its platform plugin invents -- 800px on a runner,
        # the real monitor on a desktop. At 800 every row here is above the
        # unmeasured ceiling for its tier, nothing is marked, and this test
        # fails on the machine rather than on the code it is about. Pin it:
        # what is under test is the section header, not which row a 720p-sized
        # screen would pick.
        controller._display_height = lambda: 1080
        await controller.load("movie", "tt_A")

        model = qml_module.bound(engine)["StreamModel"]
        sections = {
            model.data(model.index(row, 0), model.SectionRole) for row in range(model.rowCount())
        }
        assert sections == {"Recommended", "All sources"}

        qml_warnings.clear()  # ignore anything from bringing the app up
        component = QQmlComponent(engine, str(_QML_DIR / "Sources.qml"))
        page = component.create(engine.rootContext())
        assert page is not None, component.errorString()
        page.deleteLater()
    finally:
        del engine
    assert qml_warnings == []


def test_stream_row_marks_a_recommendation_without_warnings(
    qml_warnings: list[str],
) -> None:
    _instantiate(
        """
        import QtQuick
        import QtQuick.Controls
        import "."

        ApplicationWindow {
            width: 1280; height: 800
            StreamRow {
                width: 900
                name: "4K ⚡ ⟨Web-dl⟩"
                resolution: "4K"
                instant: true
                recommended: true
                reason: "Best 4K your connection can stream (about 100 Mbps)"
                detailText: "Silo S01E01 · 24 Mbps"
            }
        }
        """
    )
    assert qml_warnings == []


async def test_detail_builds_only_the_source_rows_on_screen(
    qml_warnings: list[str], tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The movie page is a ListView: only the source rows in view exist.

    Keeping every row alive cost twice -- a Repeater built all ~90-150 in the
    frame the model reset (150ms+ of blocked GUI thread), and the back
    button's frosted glass re-rendered all of them on every frame after (p95
    29ms while scrolling, measured). What must still hold: the rows on screen
    are the right ones and in order after the fresh list replaces the stored
    one, the bottom of the list is reachable, and none of it warns.
    """
    import time

    from PySide6.QtCore import QCoreApplication

    from gravitas.domain.models import MetaDetail, Stream

    class _Meta:
        async def __call__(self, type: str, item_id: str) -> MetaDetail:
            return MetaDetail(
                id=item_id,
                type="movie",
                name="Film",
                description=None,
                poster=None,
                background=None,
                videos=(),
            )

    def _list(prefix: str, count: int) -> list[Stream]:
        return [
            Stream(
                name=f"{prefix}{i}",
                title="",
                url=f"http://h/{prefix}{i}",
                info_hash=None,
                file_idx=None,
            )
            for i in range(count)
        ]

    class _Streams:
        def __init__(self, streams: list[Stream]) -> None:
            self.streams = streams

        async def __call__(self, type: str, item_id: str) -> list[Stream]:
            return self.streams

    def _walk(page: QQuickItem) -> list[QQuickItem]:
        # The visual tree, not findChildren: a view's delegates are parented
        # to its content item but have no QObject parent.
        items: list[QQuickItem] = []
        pending = [page]
        while pending:
            item = pending.pop()
            items.append(item)
            pending.extend(item.childItems())
        return items

    def _rows(page: QQuickItem) -> list[str]:
        rows = [
            item
            for item in _walk(page)
            if item.metaObject().className().startswith("StreamRow") and item.isVisible()
        ]
        rows.sort(key=lambda item: item.mapToItem(page, 0, 0).y())
        return [str(item.property("name")) for item in rows]

    def _pump_until(done: object, seconds: float = 5.0) -> None:
        deadline = time.monotonic() + seconds
        while not done() and time.monotonic() < deadline:  # type: ignore[operator]
            QCoreApplication.processEvents()

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = qml_module.bound(engine)["DetailController"]
        controller._get_detail = _Meta()
        streams = _Streams(_list("old", 40))
        controller._resolve_stream = streams

        qml_warnings.clear()  # ignore anything from bringing the app up
        component = QQmlComponent(engine, str(_QML_DIR / "Detail.qml"))
        # With its id: the page binds its source list only once the meta it
        # holds is this title's.
        page = component.createWithInitialProperties(
            {"mediaType": "movie", "mediaId": "tt_A"}, engine.rootContext()
        )
        assert page is not None, component.errorString()
        window = engine.rootObjects()[0]
        page.setParentItem(window.contentItem())
        page.setWidth(1200)
        page.setHeight(800)

        await controller.load("movie", "tt_A")
        streams.streams = _list("new", 40)
        await controller.load("movie", "tt_A")
        expected = [f"new{i}" for i in range(40)]

        def _on_screen_prefix() -> bool:
            rows = _rows(page)
            return bool(rows) and rows == expected[: len(rows)]

        _pump_until(_on_screen_prefix)
        built = _rows(page)
        assert built, "the rows in view are built"
        assert built == expected[: len(built)], "the rows on screen, in order"
        assert len(built) < len(expected), "rows below the fold are not built"

        view = next(
            item
            for item in _walk(page)
            if item.metaObject().className().startswith("QQuickListView")
        )
        view.positionViewAtEnd()
        _pump_until(lambda: "new39" in _rows(page))
        assert "new39" in _rows(page)
        page.deleteLater()
    finally:
        del engine
    assert qml_warnings == []


async def test_detail_stands_on_the_card_then_offers_a_source_retry(
    qml_warnings: list[str], tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A slow meta shows the clicked card's title and art, then the page; an
    addon that does not answer shows a retry, not "no sources"."""
    import asyncio
    import time

    from PySide6.QtCore import QCoreApplication

    from gravitas.domain.errors import SourcesUnavailable
    from gravitas.domain.models import MetaDetail, Stream

    gate = asyncio.Event()

    class _SlowMeta:
        async def __call__(self, type: str, item_id: str) -> MetaDetail:
            await gate.wait()
            return MetaDetail(
                id=item_id,
                type="movie",
                name="Film",
                description=None,
                poster=None,
                background=None,
                videos=(),
            )

    class _Down:
        async def __call__(self, type: str, item_id: str) -> list[Stream]:
            raise SourcesUnavailable("addon down")

    async def _pump(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            await asyncio.sleep(0.01)

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = qml_module.bound(engine)["DetailController"]
        controller._get_detail = _SlowMeta()
        controller._resolve_stream = _Down()

        qml_warnings.clear()  # ignore anything from bringing the app up
        controller.preview("tt_A", "Film", "")
        component = QQmlComponent(engine, str(_QML_DIR / "Detail.qml"))
        page = component.createWithInitialProperties(
            {"mediaType": "movie", "mediaId": "tt_A"}, engine.rootContext()
        )
        assert page is not None, component.errorString()
        window = engine.rootObjects()[0]
        page.setParentItem(window.contentItem())

        await _pump(0.3)
        assert page.property("showPreview") is True, "a slow meta stands on the card"
        gate.set()
        await _pump(0.3)
        assert page.property("metaReady") is True
        assert page.property("showPreview") is False
        assert controller.sourcesFailed is True
        page.deleteLater()
    finally:
        del engine
    assert qml_warnings == []


def test_wheel_scrolling_wakes_the_scrollbar_without_moving_the_view(
    qml_warnings: list[str],
) -> None:
    """WheelScroller keeps the view out of `moving` (so the next click lands),
    and `moving` is the only thing that makes a ScrollBar active by itself: the
    bar stayed hidden through every wheel scroll until WheelScroller told it."""
    import time

    from PySide6.QtCore import QCoreApplication, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQuick
        import QtQuick.Controls
        import "."

        Window {
            width: 400; height: 300; visible: true
            readonly property bool flashing: bar._flashing
            readonly property real viewY: view.contentY
            readonly property bool viewMoving: view.moving
            ListView {
                id: view
                anchors.fill: parent
                model: 100
                delegate: Rectangle { width: 400; height: 40 }
                WheelScroller { flick: view }
                ScrollBar.vertical: AppScrollBar { id: bar }
            }
        }
        """,
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    window = component.create()
    assert window is not None, component.errorString()
    try:
        QCoreApplication.processEvents()
        assert window.property("flashing") is False

        position = QPointF(200, 150)
        event = QWheelEvent(
            position,
            window.mapToGlobal(position),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        QCoreApplication.sendEvent(window, event)
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
        assert window.property("viewY") > 0, "the wheel scrolled the view"
        assert window.property("viewMoving") is False, "and kept it out of `moving`"
        assert window.property("flashing") is True, "the bar shows for it"

        deadline = time.monotonic() + 1.5
        while window.property("flashing") and time.monotonic() < deadline:
            QCoreApplication.processEvents()
        assert window.property("flashing") is False, "and hides again once idle"
    finally:
        window.deleteLater()
    assert qml_warnings == []


async def test_detail_loads_with_its_type_whatever_order_the_properties_arrive(
    qml_warnings: list[str], tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A property map applies mediaId before mediaType (it is sorted), and a
    load fired from onMediaIdChanged then read an empty type and opened a
    series as a movie -- no episode list, streams asked for the show id."""
    import asyncio

    from gravitas.domain.models import MetaDetail

    asked: list[tuple[str, str]] = []

    class _Meta:
        async def __call__(self, type: str, item_id: str) -> MetaDetail:
            asked.append((type, item_id))
            return MetaDetail(
                id=item_id,
                type="series",
                name="Show",
                description=None,
                poster=None,
                background=None,
                videos=(),
            )

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = qml_module.bound(engine)["DetailController"]
        controller._get_detail = _Meta()

        qml_warnings.clear()  # ignore anything from bringing the app up
        component = QQmlComponent(engine, str(_QML_DIR / "Detail.qml"))
        page = component.createWithInitialProperties(
            {"mediaType": "series", "mediaId": "tt_S"}, engine.rootContext()
        )
        assert page is not None, component.errorString()
        for _ in range(5):
            await asyncio.sleep(0)
        assert asked == [("series", "tt_S")]
        page.deleteLater()
    finally:
        del engine
    assert qml_warnings == []


async def test_detail_pages_come_and_go_without_warnings(
    qml_warnings: list[str], tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Push and pop Detail pages through Main.qml's own stack, the way the app
    navigates. Each of these once warned on every second page:

    - BackButton's tooltip bound x/y to its popup `parent`, which is cleared
      while a popped page is torn down ("Cannot read property 'width' of null").
    - The list header fed the episode count back into the list's own `model`
      through a Binding, swapping the model while that header was still being
      created ("Cannot create new component instance before completing the
      previous", then "-1 items in the process of being created" on quit).
    - A fresh page bound the shared models while they still held the previous
      title's rows, and load()'s reset cancelled the delegates it had started
      ("DelegateModel::cancel: index out range").
    """
    import asyncio
    import time

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtQml import QQmlExpression

    from gravitas.domain.models import MetaDetail, Stream, Video

    class _Meta:
        async def __call__(self, type: str, item_id: str) -> MetaDetail:
            videos = (
                tuple(
                    Video(id=f"{item_id}:1:{n}", title=f"E{n}", season=1, episode=n)
                    for n in range(1, 9)
                )
                if type == "series"
                else ()
            )
            return MetaDetail(
                id=item_id,
                type=type,  # type: ignore[arg-type]
                name=item_id,
                description=None,
                poster=None,
                background=None,
                videos=videos,
            )

    class _Streams:
        async def __call__(self, type: str, item_id: str) -> list[Stream]:
            return [
                Stream(
                    name=f"{item_id}-{n}",
                    title="t",
                    url=f"http://v/{n}",
                    info_hash=None,
                    file_idx=None,
                )
                for n in range(30)
            ]

    async def _pump(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            await asyncio.sleep(0.01)

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    engine: QQmlApplicationEngine | None = None
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        controller = qml_module.bound(engine)["DetailController"]
        controller._get_detail = _Meta()
        controller._resolve_stream = _Streams()
        root = engine.rootObjects()[0]
        context = QQmlEngine.contextForObject(root)

        def js(code: str) -> object:
            expression = QQmlExpression(context, root, code)
            value, _ = expression.evaluate()
            assert not expression.hasError(), expression.error().toString()
            return value

        async def _pump_until(condition: str, seconds: float = 3.0) -> None:
            deadline = time.monotonic() + seconds
            while not js(condition) and time.monotonic() < deadline:
                QCoreApplication.processEvents()
                await asyncio.sleep(0.005)
            assert js(condition), f"timed out waiting for: {condition}"

        await _pump(0.1)
        qml_warnings.clear()  # ignore anything from bringing the app up
        for media_type, media_id in (
            ("series", "tt_S1"),
            ("series", "tt_S2"),
            ("movie", "tt_M1"),
            ("movie", "tt_M2"),
        ):
            js(f'stack.push(detailPage, {{mediaType: "{media_type}", mediaId: "{media_id}"}})')
            # Loaded through to the list: the page shows its meta and, for a
            # film, its sources -- the states each warning came out of.
            await _pump_until("stack.currentItem.metaReady && !DetailController.streamsLoading")
            await _pump(0.05)
            js("stack.pop()")
            await _pump_until("stack.depth === 1")
            await _pump(0.05)
    finally:
        del engine
    assert qml_warnings == []


_LONG_COMBO = b"""
import QtQuick
import QtQuick.Window
import "."

Window {
    id: win
    width: 400; height: 500; visible: true
    property int chosen: 0
    property var reported: []
    AppComboBox {
        id: combo
        objectName: "combo"
        x: 10; y: 10
        model: ["Albanian", "Arabic", "Basque", "Catalan", "Dutch", "English",
                "French", "German", "Italian", "Japanese", "Polish",
                "Portuguese", "Portuguese (Brazil)", "Spanish"]
        // How every caller binds it: to its controller, moved in onActivated.
        currentIndex: win.chosen
        onActivated: (index) => { win.reported.push(index); win.chosen = index }
    }
}
"""


def _long_combo() -> tuple[QQmlEngine, QQmlComponent, object]:
    """The window with the engine and component that made it: dropping either
    destroys the window, so the caller holds all three."""
    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(_LONG_COMBO, _COMPONENTS_DIR.as_uri() + "/probe.qml")
    assert not component.isError(), component.errorString()
    win = component.create()
    assert win is not None, component.errorString()
    return engine, component, win


def _combo_in(win: object) -> QQuickItem:
    combo = win.findChild(QQuickItem, "combo")  # type: ignore[attr-defined]
    assert combo is not None
    return combo


def test_a_long_combo_ranks_what_starts_with_the_query_first(qapp: object) -> None:
    _engine, _component, win = _long_combo()
    try:
        combo = _combo_in(win)
        assert combo.property("searchable") is True
        combo.setProperty("_query", "po")
        assert combo.property("_matches").toVariant() == [10, 11, 12]
        # A word inside the name, then plain containment.
        combo.setProperty("_query", "bra")
        assert combo.property("_matches").toVariant() == [12]
        combo.setProperty("_query", "PANISH")
        assert combo.property("_matches").toVariant() == [13]
    finally:
        win.deleteLater()  # type: ignore[attr-defined]


def test_typing_into_a_long_combo_picks_through_the_callers_binding(qapp: object) -> None:
    """Typed straight after the click, while the menu is still fading in: the
    field takes focus before the animation, so those keys are not lost. The
    pick arrives as activated(index) and moves currentIndex through the
    caller's own binding, which must survive it."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    _engine, _component, win = _long_combo()
    try:
        combo = _combo_in(win)
        QTest.mouseClick(win, Qt.MouseButton.LeftButton, pos=QPoint(30, 25))  # type: ignore[arg-type]
        for key in (Qt.Key.Key_B, Qt.Key.Key_R, Qt.Key.Key_A):
            QTest.keyClick(win, key)  # type: ignore[arg-type]
        QTest.keyClick(win, Qt.Key.Key_Return)  # type: ignore[arg-type]
        assert win.property("reported").toVariant() == [12]  # type: ignore[attr-defined]
        assert combo.property("currentIndex") == 12
        # Still bound: a change from the caller's side lands.
        win.setProperty("chosen", 3)  # type: ignore[attr-defined]
        assert combo.property("currentIndex") == 3
    finally:
        win.deleteLater()  # type: ignore[attr-defined]


def test_a_short_combo_keeps_the_plain_list(qapp: object) -> None:
    engine = QQmlEngine()
    engine.addImportPath(str(_QML_DIR))
    component = QQmlComponent(engine)
    component.setData(
        b'import QtQuick\nimport "."\nAppComboBox { model: ["Default", "Name", "Newest"] }',
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    combo = component.create()
    assert combo is not None, component.errorString()
    try:
        assert combo.property("searchable") is False
    finally:
        combo.deleteLater()


def test_an_open_short_combo_keeps_its_field_and_shows_every_entry(qapp: object) -> None:
    """The plain list must not share a view with the search list: a view that
    names a `delegate` writes it into the ComboBox's own delegate model, which
    made every row read the first entry and blanked the field while open."""
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
            width: 360; height: 220; visible: true
            AppComboBox {
                objectName: "combo"
                x: 20; y: 20; width: 220
                model: ["Popular (Cinemeta)", "New (Cinemeta)", "Featured (Cinemeta)"]
            }
        }
        """,
        _COMPONENTS_DIR.as_uri() + "/probe.qml",
    )
    win = component.create()
    assert win is not None, component.errorString()
    try:
        combo = _combo_in(win)
        QTest.mouseClick(win, Qt.MouseButton.LeftButton, pos=QPoint(100, 38))  # type: ignore[arg-type]
        QTest.qWait(50)
        assert combo.property("displayText") == "Popular (Cinemeta)"

        texts: list[str] = []

        def walk(item: QQuickItem) -> None:
            for child in item.childItems():
                if child.metaObject().className() == "QQuickText" and child.isVisible():
                    texts.append(child.property("text"))
                walk(child)

        walk(win.contentItem().parentItem() or win.contentItem())  # type: ignore[attr-defined]
        for entry in ("Popular (Cinemeta)", "New (Cinemeta)", "Featured (Cinemeta)"):
            assert entry in texts, texts
    finally:
        win.deleteLater()


def test_a_long_combo_opens_already_scrolled_to_the_chosen_entry(qapp: object) -> None:
    """Centred before the open animation, not after it: centring once opened
    showed the chosen entry at the bottom edge first and then jumped."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    _engine, _component, win = _long_combo()
    try:
        win.setProperty("chosen", 12)  # type: ignore[attr-defined]

        def scroll() -> list[float]:
            found: list[float] = []

            def walk(item: QQuickItem) -> None:
                for child in item.childItems():
                    if (
                        child.metaObject().className() == "QQuickListView"
                        and child.isVisible()
                        and child.property("count") > 0
                    ):
                        found.append(child.property("contentY"))
                    walk(child)

            walk(win.contentItem())  # type: ignore[attr-defined]
            return found

        QTest.mouseClick(win, Qt.MouseButton.LeftButton, pos=QPoint(30, 25))  # type: ignore[arg-type]
        QTest.qWait(10)
        first = scroll()
        QTest.qWait(400)
        assert first and scroll() == first
    finally:
        win.deleteLater()  # type: ignore[attr-defined]
