import asyncio

from gravitas.main import build_app


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
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)


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
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Detail.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Detail.qml failed to load: {component.errorString()}"


def test_player_qml_loads(qapp: object) -> None:
    from pathlib import Path

    from PySide6.QtCore import QObject
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import gravitas.main as gmain

    engine = QQmlEngine()
    engine.rootContext().setContextProperty("playerController", QObject())
    qml = Path(gmain.__file__).parent / "presentation" / "qml" / "Player.qml"
    component = QQmlComponent(engine, str(qml))
    obj = component.create()
    assert obj is not None, f"Player.qml failed to load: {component.errorString()}"
