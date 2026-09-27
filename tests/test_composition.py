import asyncio
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from gravitas.main import build_app
from gravitas.presentation import qml_module


@pytest.fixture(autouse=True)
def _isolated_progress_db(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # build_app() constructs a WatchProgressRepository backed by a real
    # SqliteProgressStore, which eagerly opens (and creates) its database file
    # on construction. Every test in this module that calls build_app() must
    # not touch the developer's real ~/.local/share/gravitas/progress.db.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_build_app_binds_the_qml_singletons(qapp: object) -> None:
    # build_app() only wires the controllers/models into the QML context and
    # returns -- it stays asyncio-free so it can be exercised without a
    # running loop (as here). The Cinemeta bootstrap (install + first catalog
    # refresh) is a qasync coroutine that gravitas.main.main() schedules onto
    # the qasync loop via loop.create_task(bootstrap()) after build_app()
    # returns, so no task is scheduled during this test. The event loop is
    # still set up here (mirroring main(), which calls
    # asyncio.set_event_loop(loop) before build_app()) in case any wiring
    # ever needs one; nothing is scheduled onto it, and the finally block's
    # drain is a no-op safety net.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
        bound = qml_module.bound(engine)
        assert bound.get("CatalogController") is not None
        assert bound.get("DetailController") is not None
        assert bound.get("PlayerController") is not None
        assert bound.get("AddonController") is not None
        assert bound.get("CatalogRowsModel") is not None
        assert bound.get("StreamModel") is not None
        assert bound.get("DiscoverController") is not None
        assert bound.get("DiscoverModel") is not None
        assert bound.get("DiscoverProxy") is not None
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)


def test_settings_context_properties_present(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app([], DEFAULT_ADDON)
    bound = qml_module.bound(engine)
    assert bound.get("SettingsController") is not None
    assert bound.get("AddonListModel") is not None


def test_discover_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()

    class _StubModel(QObject):
        pass

    stub = _StubModel()
    qml_module.bind(engine, {"DiscoverController": stub})
    qml_module.bind(engine, {"DiscoverModel": stub})
    qml_module.bind(engine, {"DiscoverProxy": stub})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Discover.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Discover.qml failed to load: {component.errorString()}"


def test_detail_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    stub = QObject()
    qml_module.bind(engine, {"DetailController": stub})
    qml_module.bind(engine, {"StreamModel": stub})
    qml_module.bind(engine, {"EpisodeModel": stub})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Detail.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Detail.qml failed to load: {component.errorString()}"


def test_player_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject, QtMsgType, Slot, qInstallMessageHandler
    from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterType

    import gravitas.main as gmain
    from gravitas.presentation.video.mpv_item import MpvVideoItem

    # Registration is idempotent per process; needed when this test runs alone.
    qmlRegisterType(MpvVideoItem, "Gravitas", 1, 0, "MpvVideo")  # type: ignore[call-overload]
    engine = QQmlEngine()

    class _StubPlayerController(QObject):
        # Called from MpvVideo's Component.onCompleted even with no playback.
        @Slot(QObject)
        def attachVideo(self, item: QObject) -> None:
            pass

    # Named variable, not an inline temporary: qml_module.bind does not
    # take ownership, and a GC'd stub reads back as null — which then fails
    # this test's own no-TypeError assertion for the wrong reason.
    stub = _StubPlayerController()
    qml_module.bind(engine, {"PlayerController": stub})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Player.qml"
    component = QQmlComponent(engine, str(qml))
    # Creation-time TypeErrors (e.g. an attached property whose import is
    # missing) leave the component loadable but the feature dead — the hotkey
    # gate did exactly this once. Collect warnings and fail on them.
    warnings: list[str] = []

    def handler(mode: QtMsgType, _context: object, message: str) -> None:
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            warnings.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        obj = component.create()
    finally:
        qInstallMessageHandler(previous)
    assert obj is not None, f"Player.qml failed to load: {component.errorString()}"
    errors = [w for w in warnings if "TypeError" in w or "ReferenceError" in w]
    assert errors == [], f"Player.qml loaded with runtime errors: {errors}"


def test_player_format_marks_never_run_into_the_title(qapp: object) -> None:
    """A narrow window drops marks from the end of the list (channels, then
    immersive audio, then resolution) rather than laying them over the
    centred title, and shows them all when there is room."""
    from pathlib import Path

    from PySide6.QtCore import Property, QObject, Signal, Slot
    from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterType
    from PySide6.QtQuick import QQuickItem

    import gravitas.main as gmain
    from gravitas.presentation.video.mpv_item import MpvVideoItem

    qmlRegisterType(MpvVideoItem, "Gravitas", 1, 0, "MpvVideo")  # type: ignore[call-overload]
    engine = QQmlEngine()

    class _Controller(QObject):
        changed = Signal()

        @Slot(QObject)
        def attachVideo(self, item: QObject) -> None:
            pass

        @Property(str, notify=changed)
        def mediaTitle(self) -> str:
            return "Silo"

        @Property(str, notify=changed)
        def mediaLabel(self) -> str:
            return "S1E1 · Freedom Day"

        @Property("QVariantList", notify=changed)  # type: ignore[arg-type]
        def mediaBadges(self) -> list[dict[str, str]]:
            return [
                {"format": "dolby-vision", "label": "Dolby Vision", "detail": ""},
                {"format": "4k", "label": "4K", "detail": ""},
                {"format": "dolby-atmos", "label": "Dolby Atmos", "detail": ""},
                {"format": "channels", "label": "5.1", "detail": ""},
            ]

    stub = _Controller()
    qml_module.bind(engine, {"PlayerController": stub})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Player.qml"
    component = QQmlComponent(engine, str(qml))
    page = component.create()
    assert page is not None, component.errorString()
    page.setProperty("height", 600)
    row = page.findChild(QQuickItem, "formatMarks")
    assert row is not None

    def shown(width: int) -> int:
        page.setProperty("width", width)
        qapp.processEvents()  # type: ignore[attr-defined]
        marks = [c for c in row.childItems() if c.property("format")]
        assert len(marks) == 4
        return sum(1 for c in marks if c.isVisible())

    def clear_of_title() -> bool:
        return float(row.property("x")) >= float(row.property("leftEdge")) - 0.5

    assert shown(1600) == 4
    assert clear_of_title()
    counts = []
    for width in (1000, 800, 600, 420):
        counts.append(shown(width))
        assert clear_of_title(), f"over the title at {width}px"
    assert counts == sorted(counts, reverse=True), "narrower never shows more"
    assert counts[-1] < 4, "a narrow window drops marks"
    assert shown(330) == 0, "no room at all: none, rather than over the title"


def test_settings_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()

    class _StubModel(QObject):
        pass

    stub = _StubModel()
    qml_module.bind(engine, {"AddonController": stub})
    qml_module.bind(engine, {"SettingsController": stub})
    qml_module.bind(engine, {"AddonListModel": stub})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Settings.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Settings.qml failed to load: {component.errorString()}"


def test_onboarding_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject, QtMsgType, qInstallMessageHandler
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    onboarding_stub = QObject()
    addon_stub = QObject()
    qml_module.bind(engine, {"OnboardingController": onboarding_stub})
    qml_module.bind(engine, {"AddonController": addon_stub})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Onboarding.qml"
    component = QQmlComponent(engine, str(qml))
    # Main.qml only loads this page for fresh installs, so the always-run
    # composition test never parses it — verify it standalone, and fail on
    # creation-time TypeErrors/ReferenceErrors like the Player test does.
    warnings: list[str] = []

    def handler(mode: QtMsgType, _context: object, message: str) -> None:
        if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
            warnings.append(message)

    previous = qInstallMessageHandler(handler)
    try:
        obj = component.create()
    finally:
        qInstallMessageHandler(previous)
    assert obj is not None, f"Onboarding.qml failed to load: {component.errorString()}"
    errors = [w for w in warnings if "TypeError" in w or "ReferenceError" in w]
    assert errors == [], f"Onboarding.qml loaded with runtime errors: {errors}"


def test_search_context_properties_present(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app(argv=[], default_addon_url=DEFAULT_ADDON)
    bound = qml_module.bound(engine)
    assert bound.get("SearchController") is not None
    assert bound.get("SearchResultsModel") is not None
    assert bound.get("SearchPageModel") is not None


def test_search_results_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    # Held in names: bind() does not take ownership either.
    results, page = QObject(), QObject()
    qml_module.bind(engine, {"SearchResultsModel": results, "SearchPageModel": page})
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "SearchResults.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"SearchResults.qml failed to load: {component.errorString()}"


def test_build_app_wires_watch_progress(
    qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
        bound = qml_module.bound(engine)
        assert bound.get("ProgressController") is not None
        assert bound.get("WatchedListModel") is not None
        # QML does not take ownership; without a surviving Python reference
        # these read back as null in QML.
        names = {type(ref).__name__ for ref in engine._gravitas_refs}
        assert "ProgressController" in names
        assert "WatchedListModel" in names

        # The player writes progress on a path that never touches
        # ProgressController's own mutations (see main.py's comment on the
        # progressRecorded -> notifyRecorded connection): without that wire,
        # `revision` goes stale even though the bars themselves refresh fine.
        # Exercise the actual signal, not just the presence of both objects.
        player_controller = bound.get("PlayerController")
        progress_controller = bound.get("ProgressController")
        revision_before = progress_controller.revision
        player_controller.progressRecorded.emit()
        assert progress_controller.revision == revision_before + 1
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def test_build_app_rebuilds_continue_watching_on_progress_change(
    qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The row's membership changes when a title is finished or forgotten, so
    it must be rebuilt on progressChanged -- not only its bars refreshed. This
    is one connect line in build_app(); without a test, reverting it leaves the
    whole suite green and the row silently stale."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        bound = qml_module.bound(engine)
        rows_model = bound.get("CatalogRowsModel")
        progress_controller = bound.get("ProgressController")

        calls: list[int] = []
        original = rows_model.set_continue_watching
        rows_model.set_continue_watching = lambda entries: (  # type: ignore[method-assign]
            calls.append(len(entries)),
            original(entries),
        )[1]

        progress_controller.progressChanged.emit()
        assert calls, "progressChanged did not rebuild the Continue Watching row"
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def test_build_app_wires_watchlist(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app([], DEFAULT_ADDON)
    assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
    bound = qml_module.bound(engine)
    watchlist_controller = bound.get("WatchlistController")
    movies_model = bound.get("WatchlistMoviesModel")
    series_model = bound.get("WatchlistSeriesModel")
    assert watchlist_controller is not None
    assert movies_model is not None
    assert series_model is not None
    # QML does not take ownership; without a surviving Python reference
    # these read back as null in QML.
    names = {type(ref).__name__ for ref in engine._gravitas_refs}
    assert "WatchlistController" in names

    # Toggling through the controller must land in the section model the
    # page's Repeater reads — a movie in Movies, not Series.
    rows_before = movies_model.rowCount()
    watchlist_controller.toggle(
        {"mediaId": "tt-test", "type": "movie", "name": "T", "poster": "", "year": ""}
    )
    assert movies_model.rowCount() == rows_before + 1
    assert series_model.rowCount() == 0
    watchlist_controller.toggle(
        {"mediaId": "tt-test", "type": "movie", "name": "T", "poster": "", "year": ""}
    )
    assert movies_model.rowCount() == rows_before


def test_build_app_wires_trakt(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app([], DEFAULT_ADDON)
    assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
    bound = qml_module.bound(engine)
    trakt_controller = bound.get("TraktController")
    assert trakt_controller is not None
    names = {type(ref).__name__ for ref in engine._gravitas_refs}
    assert "TraktController" in names
    # The scrobble wire: player lifecycle events must reach the Trakt sink.
    player_controller = bound.get("PlayerController")
    receivers = player_controller.receivers("2scrobbleEvent(QString,QVariantMap,double,double)")
    assert receivers >= 1


def test_build_app_wires_onboarding(qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    from gravitas.infrastructure.settings.json_store import JsonSettingsStore
    from gravitas.main import DEFAULT_ADDON, build_app

    # A pristine config dir: this launch is a fresh install.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _, engine = build_app([], DEFAULT_ADDON)
    assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
    bound = qml_module.bound(engine)
    controller = bound.get("OnboardingController")
    assert controller is not None
    names = {type(ref).__name__ for ref in engine._gravitas_refs}
    assert "OnboardingController" in names

    assert controller.active is True
    controller.complete()
    assert controller.active is False
    # complete() must route through SettingsController.persist with the
    # onboarding holder wired, or the next launch shows the wizard again.
    assert JsonSettingsStore().load().onboarding_done is True


def test_deep_link_controller_is_wired_into_qml(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app([], DEFAULT_ADDON)
    assert engine.rootObjects(), "Main.qml failed to load"
    assert qml_module.bound(engine)["DeepLinkController"] is not None


def test_deep_link_listener_is_kept_alive_on_the_engine(qapp: object) -> None:
    # QML does not take ownership and nothing else references
    # the listener; collected, it would stop delivering links with no error.
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app([], DEFAULT_ADDON)
    assert engine._gravitas_deep_links is not None


def test_pending_link_finds_a_stremio_argument() -> None:
    from gravitas.main import pending_link

    assert pending_link(["gravitas", "stremio://a/manifest.json"]) == "stremio://a/manifest.json"
    assert pending_link(["gravitas", "STREMIO://a/manifest.json"]) == "STREMIO://a/manifest.json"


def test_pending_link_ignores_ordinary_launches() -> None:
    from gravitas.main import pending_link

    assert pending_link(["gravitas"]) is None
    assert pending_link([]) is None
    # argv[0] is never a link, even if someone names the binary oddly.
    assert pending_link(["stremio://not-an-arg/manifest.json"]) is None
    assert pending_link(["gravitas", "--debug", "https://example/manifest.json"]) is None
