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
