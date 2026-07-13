"""Composition root: wire adapters into use cases and launch the QML app."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import qasync  # type: ignore[import-untyped]
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.browse_board import BrowseBoard
from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.resolve_media_link import ResolveMediaLink
from gravitas.application.resolve_stream import ResolveStream
from gravitas.application.search_media import SearchMedia
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.domain.ports import MediaPlayer
from gravitas.infrastructure.addons.client import AddonClient
from gravitas.infrastructure.metadata.tmdb_resolver import TmdbResolver
from gravitas.infrastructure.player.mpv_player import MpvPlayer
from gravitas.presentation.controllers.addon_controller import AddonController
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.controllers.player_controller import PlayerController
from gravitas.presentation.controllers.search_controller import SearchController
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.models.addon_list_model import AddonListModel
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.search_results_model import SearchResultsModel
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

    # Bundle a clean UI font (Inter) and make it the application default so
    # every QML Text inherits it without per-component wiring.
    font_id = QFontDatabase.addApplicationFont(str(_QML_DIR / "assets" / "Inter.ttf"))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0]))

    http = httpx.AsyncClient()
    source = AddonClient(http)
    repo = AddonRepository(source)

    class _TmdbKeyHolder:
        key: str | None = None

    tmdb_key = _TmdbKeyHolder()
    tmdb_resolver = TmdbResolver(http, lambda: tmdb_key.key)

    rows_model = CatalogRowsModel()
    stream_model = StreamListModel()

    discover_model = PosterGridModel()
    discover_controller = DiscoverController(BrowseBoard(repo), repo, discover_model)

    catalog_controller = CatalogController(BrowseCatalog(repo), rows_model)
    detail_controller = DetailController(GetDetail(repo), ResolveStream(repo), stream_model)
    install_addon = InstallAddon(repo)
    addon_controller = AddonController(install_addon, catalog_controller)
    addon_list_model = AddonListModel()
    settings_controller = SettingsController(
        UninstallAddon(repo), repo, addon_list_model, catalog_controller, tmdb_key
    )
    # Keep the Settings list in sync after a user installs a new addon.
    addon_controller.addonInstalled.connect(lambda _name: settings_controller.refreshAddons())

    search_results_model = SearchResultsModel()
    search_page_model = SearchResultsModel()
    search_controller = SearchController(
        SearchMedia(repo),
        ResolveMediaLink(repo, tmdb_resolver),
        search_results_model,
        search_page_model,
    )

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
    ctx.setContextProperty("settingsController", settings_controller)
    ctx.setContextProperty("addonListModel", addon_list_model)
    ctx.setContextProperty("searchController", search_controller)
    ctx.setContextProperty("searchResultsModel", search_results_model)
    ctx.setContextProperty("searchPageModel", search_page_model)

    async def bootstrap() -> None:
        # Install the default addon as protected (non-removable), then bring
        # the UI up to date deterministically: load the catalog rows and prime
        # the Settings addon list before bootstrap() returns.
        await install_addon(default_addon_url, protected=True)
        await catalog_controller.load_catalog()
        settings_controller.refreshAddons()

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
        settings_controller,
        search_controller,
        rows_model,
        discover_model,
        stream_model,
        addon_list_model,
        search_results_model,
        search_page_model,
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
