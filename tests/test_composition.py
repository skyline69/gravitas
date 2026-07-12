import asyncio

from gravitas.main import build_app


def test_build_app_registers_context_properties(qapp: object) -> None:
    # Home.qml's `Component.onCompleted: catalogController.refresh()` fires
    # synchronously during engine.load() inside build_app(). refresh() is a real
    # qasync.asyncSlot, so it needs a set (not necessarily running) event loop to
    # schedule its task onto -- mirrors gravitas.main.main(), which calls
    # asyncio.set_event_loop(loop) before build_app(). The loop is never run here;
    # the scheduled task is cancelled and drained in the finally block.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _app, engine = build_app(
            argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
        )
        ctx = engine.rootContext()
        assert ctx.contextProperty("catalogController") is not None
        assert ctx.contextProperty("detailController") is not None
        assert ctx.contextProperty("playerController") is not None
        assert ctx.contextProperty("posterModel") is not None
        assert ctx.contextProperty("streamModel") is not None
    finally:
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        asyncio.set_event_loop(None)
