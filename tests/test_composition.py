import asyncio
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from gravitas.main import build_app


@pytest.fixture(autouse=True)
def _isolated_progress_db(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # build_app() constructs a WatchProgressRepository backed by a real
    # SqliteProgressStore, which eagerly opens (and creates) its database file
    # on construction. Every test in this module that calls build_app() must
    # not touch the developer's real ~/.local/share/gravitas/progress.db.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))


def test_build_app_registers_context_properties(qapp: object) -> None:
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
        ctx = engine.rootContext()
        assert ctx.contextProperty("catalogController") is not None
        assert ctx.contextProperty("detailController") is not None
        assert ctx.contextProperty("playerController") is not None
        assert ctx.contextProperty("addonController") is not None
        assert ctx.contextProperty("catalogRowsModel") is not None
        assert ctx.contextProperty("streamModel") is not None
        assert ctx.contextProperty("discoverController") is not None
        assert ctx.contextProperty("discoverModel") is not None
        assert ctx.contextProperty("discoverProxy") is not None
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
    ctx = engine.rootContext()
    assert ctx.contextProperty("settingsController") is not None
    assert ctx.contextProperty("addonListModel") is not None


def test_discover_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()

    class _StubModel(QObject):
        pass

    stub = _StubModel()
    engine.rootContext().setContextProperty("discoverController", stub)
    engine.rootContext().setContextProperty("discoverModel", stub)
    engine.rootContext().setContextProperty("discoverProxy", stub)
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
    engine.rootContext().setContextProperty("detailController", stub)
    engine.rootContext().setContextProperty("streamModel", stub)
    engine.rootContext().setContextProperty("episodeModel", stub)
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Detail.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Detail.qml failed to load: {component.errorString()}"


def test_player_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterType

    import gravitas.main as gmain
    from gravitas.presentation.video.mpv_item import MpvVideoItem

    # Registration is idempotent per process; needed when this test runs alone.
    qmlRegisterType(MpvVideoItem, "Gravitas", 1, 0, "MpvVideo")  # type: ignore[call-overload]
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("playerController", QObject())
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Player.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Player.qml failed to load: {component.errorString()}"


def test_settings_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()

    class _StubModel(QObject):
        pass

    stub = _StubModel()
    engine.rootContext().setContextProperty("addonController", stub)
    engine.rootContext().setContextProperty("settingsController", stub)
    engine.rootContext().setContextProperty("addonListModel", stub)
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Settings.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Settings.qml failed to load: {component.errorString()}"


def test_search_context_properties_present(qapp: object) -> None:
    from gravitas.main import DEFAULT_ADDON, build_app

    _, engine = build_app(argv=[], default_addon_url=DEFAULT_ADDON)
    ctx = engine.rootContext()
    assert ctx.contextProperty("searchController") is not None
    assert ctx.contextProperty("searchResultsModel") is not None
    assert ctx.contextProperty("searchPageModel") is not None


def test_search_results_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    engine.rootContext().setContextProperty("searchResultsModel", QObject())
    engine.rootContext().setContextProperty("searchPageModel", QObject())
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "SearchResults.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"SearchResults.qml failed to load: {component.errorString()}"


def test_build_app_wires_watch_progress(
    qapp: object, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    # Never touch the developer's real progress database from a test.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        assert engine.rootObjects(), "Main.qml failed to load (QML parse/type error)"
        ctx = engine.rootContext()
        assert ctx.contextProperty("progressController") is not None
        assert ctx.contextProperty("watchedListModel") is not None
        # setContextProperty does not take ownership; without a surviving
        # Python reference these read back as null in QML.
        names = {type(ref).__name__ for ref in engine._gravitas_refs}
        assert "ProgressController" in names
        assert "WatchedListModel" in names

        # The player writes progress on a path that never touches
        # ProgressController's own mutations (see main.py's comment on the
        # progressRecorded -> notifyRecorded connection): without that wire,
        # `revision` goes stale even though the bars themselves refresh fine.
        # Exercise the actual signal, not just the presence of both objects.
        player_controller = ctx.contextProperty("playerController")
        progress_controller = ctx.contextProperty("progressController")
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
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        ctx = engine.rootContext()
        rows_model = ctx.contextProperty("catalogRowsModel")
        progress_controller = ctx.contextProperty("progressController")

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
