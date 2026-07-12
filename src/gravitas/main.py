"""Composition root: wire adapters into use cases and launch the QML app."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import qasync  # type: ignore[import-untyped]
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.browse_board import BrowseBoard
from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.resolve_stream import ResolveStream
from gravitas.domain.ports import MediaPlayer
from gravitas.infrastructure.addons.client import AddonClient
from gravitas.infrastructure.player.mpv_player import MpvPlayer
from gravitas.presentation.controllers.addon_controller import AddonController
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.controllers.player_controller import PlayerController
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.stream_list_model import StreamListModel

_QML_DIR = Path(__file__).parent / "presentation" / "qml"
DEFAULT_ADDON = "https://v3-cinemeta.strem.io/manifest.json"


def build_app(
    argv: list[str], default_addon_url: str
) -> tuple[QGuiApplication, QQmlApplicationEngine]:
    instance = QGuiApplication.instance()
    app = instance if isinstance(instance, QGuiApplication) else QGuiApplication(argv)

    # The native (macOS/Windows) Quick Controls style silently ignores
    # background/contentItem/indicator customization, so our themed App*
    # components would fall back to native rendering. Basic is fully
    # customizable. Must be set before any Controls type is instantiated.
    QQuickStyle.setStyle("Basic")

    http = httpx.AsyncClient()
    source = AddonClient(http)
    repo = AddonRepository(source)

    rows_model = CatalogRowsModel()
    stream_model = StreamListModel()

    discover_model = PosterGridModel()
    discover_controller = DiscoverController(BrowseBoard(repo), repo, discover_model)

    catalog_controller = CatalogController(BrowseCatalog(repo), rows_model)
    detail_controller = DetailController(GetDetail(repo), ResolveStream(repo), stream_model)
    addon_controller = AddonController(InstallAddon(repo), detail_controller, catalog_controller)

    engine = QQmlApplicationEngine()

    def make_player() -> MediaPlayer:
        root_objects = engine.rootObjects()
        window = root_objects[0] if root_objects else None
        window_id = int(window.winId()) if isinstance(window, QQuickWindow) else 0
        return MpvPlayer(window_id=window_id)

    player_controller = PlayerController(make_player)

    ctx = engine.rootContext()
    ctx.setContextProperty("catalogController", catalog_controller)
    ctx.setContextProperty("detailController", detail_controller)
    ctx.setContextProperty("playerController", player_controller)
    ctx.setContextProperty("addonController", addon_controller)
    ctx.setContextProperty("catalogRowsModel", rows_model)
    ctx.setContextProperty("streamModel", stream_model)
    ctx.setContextProperty("discoverController", discover_controller)
    ctx.setContextProperty("discoverModel", discover_model)

    async def bootstrap() -> None:
        # Reuse the same install-bind-refresh path AddonController.addAddon
        # exposes to QML, so there is a single deterministic code path for
        # installing an addon and bringing the UI up to date. addAddon is a
        # qasync asyncSlot, which returns the underlying asyncio Task -- await
        # it here to keep cold-start ordering deterministic (manifest bound
        # and catalog loaded before bootstrap() returns).
        await addon_controller.addAddon(default_addon_url)

    engine.load(str(_QML_DIR / "Main.qml"))

    # setContextProperty does not take ownership of the QObject in PySide6: if
    # no Python reference to these controllers/models survives past this
    # function, they are garbage-collected and the QML context properties
    # silently read back as null. Keep them (and the httpx client, and the
    # bootstrap coroutine) alive for the lifetime of the engine. `main()`
    # schedules `bootstrap` on the running event loop once one exists;
    # `build_app` itself must stay asyncio-free so it can be exercised
    # without a loop (e.g. in tests).
    engine._gravitas_refs = (  # type: ignore[attr-defined]
        catalog_controller,
        detail_controller,
        player_controller,
        addon_controller,
        discover_controller,
        rows_model,
        discover_model,
        stream_model,
    )
    engine._gravitas_bootstrap = bootstrap  # type: ignore[attr-defined]
    engine._gravitas_http = http  # type: ignore[attr-defined]
    return app, engine


def main() -> int:
    app = QGuiApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    _, engine = build_app(sys.argv, DEFAULT_ADDON)
    if not engine.rootObjects():
        return 1
    with loop:
        bootstrap: Callable[[], Awaitable[None]] = engine._gravitas_bootstrap  # type: ignore[attr-defined]
        loop.create_task(bootstrap())
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
