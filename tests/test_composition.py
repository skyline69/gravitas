from gravitas.main import build_app


def test_build_app_registers_context_properties(qapp: object) -> None:
    _app, engine = build_app(
        argv=[], default_addon_url="https://v3-cinemeta.strem.io/manifest.json"
    )
    ctx = engine.rootContext()
    assert ctx.contextProperty("catalogController") is not None
    assert ctx.contextProperty("detailController") is not None
    assert ctx.contextProperty("playerController") is not None
    assert ctx.contextProperty("posterModel") is not None
    assert ctx.contextProperty("streamModel") is not None
