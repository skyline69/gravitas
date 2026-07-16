"""Composition root: wire adapters into use cases and launch the QML app."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import qasync  # type: ignore[import-untyped]
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.browse_board import BrowseBoard
from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.application.continue_watching import ContinueWatching
from gravitas.application.get_detail import GetDetail
from gravitas.application.install_addon import InstallAddon
from gravitas.application.preview_addon import PreviewAddon
from gravitas.application.resolve_media_link import ResolveMediaLink
from gravitas.application.resolve_stream import ResolveStream
from gravitas.application.search_media import SearchMedia
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import SubtitleStyle
from gravitas.domain.ports import MediaPlayer
from gravitas.infrastructure.addons.client import AddonClient
from gravitas.infrastructure.cache.network_cache import CachingNetworkAccessManagerFactory
from gravitas.infrastructure.desktop.url_scheme import (
    DeepLinkListener,
    forward_to_running_instance,
)
from gravitas.infrastructure.metadata.tmdb_resolver import TmdbResolver
from gravitas.infrastructure.player.mpv_player import MpvPlayer
from gravitas.infrastructure.progress.sqlite_store import SqliteProgressStore
from gravitas.infrastructure.settings.json_store import JsonSettingsStore
from gravitas.presentation.controllers.addon_controller import AddonController
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.controllers.deep_link_controller import DeepLinkController
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.controllers.player_controller import PlayerController
from gravitas.presentation.controllers.progress_controller import ProgressController
from gravitas.presentation.controllers.search_controller import SearchController
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.models.addon_list_model import AddonListModel
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.poster_grid_proxy import PosterGridProxy
from gravitas.presentation.models.search_results_model import SearchResultsModel
from gravitas.presentation.models.stream_list_model import StreamListModel
from gravitas.presentation.models.watched_list_model import WatchedListModel

_log = logging.getLogger(__name__)

_QML_DIR = Path(__file__).parent / "presentation" / "qml"
DEFAULT_ADDON = "https://v3-cinemeta.strem.io/manifest.json"
_LINK_SCHEME = "stremio://"


def pending_link(argv: list[str]) -> str | None:
    """The stremio:// URL this process was launched with, if any.

    Linux browsers hand a registered scheme over as `gravitas <url>`. macOS
    never does -- it posts a QFileOpenEvent instead -- so this returns None
    there and DeepLinkListener carries it.
    """
    for arg in argv[1:]:
        if arg.lower().startswith(_LINK_SCHEME):
            return arg
    return None


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

    # The in-scene mpv renderer (QQuickFramebufferObject + MpvRenderContext)
    # only works on the OpenGL scene-graph backend; don't let Qt pick another
    # RHI. Must run before the first QQuickWindow is created.
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)

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

    settings_store = JsonSettingsStore()
    persisted = settings_store.load()

    progress_repo = WatchProgressRepository(SqliteProgressStore())
    # Once per launch, right after the table is indexed and before anything
    # reads it. Startup is the only cost that scales with the table, so this is
    # exactly where bounding it pays -- and doing it here rather than on every
    # write keeps the 5s playback tick a single UPSERT.
    pruned = progress_repo.prune()
    if pruned:
        _log.info("pruned %d old watched progress rows", pruned)

    tmdb_key = _TmdbKeyHolder()
    tmdb_key.key = persisted.tmdb_key
    tmdb_resolver = TmdbResolver(http, lambda: tmdb_key.key)

    class _SubStyleHolder:
        style: SubtitleStyle = SubtitleStyle()

    sub_style = _SubStyleHolder()
    sub_style.style = persisted.subtitle_style

    rows_model = CatalogRowsModel(progress_repo)
    stream_model = StreamListModel()

    discover_model = PosterGridModel(progress_repo)
    discover_proxy = PosterGridProxy(discover_model)
    discover_controller = DiscoverController(BrowseBoard(repo), repo, discover_model)

    catalog_controller = CatalogController(BrowseCatalog(repo), rows_model)
    episode_model = EpisodeListModel(progress_repo)
    detail_controller = DetailController(
        GetDetail(repo), ResolveStream(repo), stream_model, episode_model, progress_repo
    )
    install_addon = InstallAddon(repo)
    addon_controller = AddonController(install_addon, catalog_controller)
    deep_link_controller = DeepLinkController(PreviewAddon(source), addon_controller)
    addon_list_model = AddonListModel()
    settings_controller = SettingsController(
        UninstallAddon(repo),
        repo,
        addon_list_model,
        catalog_controller,
        tmdb_key,
        settings_store,
        sub_style,
    )

    # Keep the Settings list in sync — and the settings file current — after a
    # user installs a new addon.
    def _on_addon_installed(_name: str) -> None:
        settings_controller.refreshAddons()
        settings_controller.persist()

    addon_controller.addonInstalled.connect(_on_addon_installed)

    search_results_model = SearchResultsModel(progress_repo)
    search_page_model = SearchResultsModel(progress_repo)
    search_controller = SearchController(
        SearchMedia(repo),
        ResolveMediaLink(repo, tmdb_resolver),
        search_results_model,
        search_page_model,
        repo.search_stream,
    )

    # Must be registered before the engine parses any QML that mentions it.
    from gravitas.presentation.video.mpv_item import MpvVideoItem

    qmlRegisterType(MpvVideoItem, "Gravitas", 1, 0, "MpvVideo")  # type: ignore[call-overload]

    engine = QQmlApplicationEngine()

    # Artwork is fetched by QML's Image through Qt's network stack, which never
    # reaches Python -- so posters re-downloaded on every launch. Must be
    # installed before any QML loads, or the first requests bypass it. Like a
    # context property, the factory is not owned by Qt: without a surviving
    # Python reference it is collected and every request silently misses.
    nam_factory = CachingNetworkAccessManagerFactory()
    engine.setNetworkAccessManagerFactory(nam_factory)

    def make_player() -> MediaPlayer:
        return MpvPlayer()

    watched_model = WatchedListModel()
    progress_controller = ProgressController(progress_repo, watched_model)

    player_controller = PlayerController(make_player, lambda: sub_style.style, progress_repo)
    # Live-apply subtitle style edits to an active player.
    settings_controller.subtitleStyleChanged.connect(player_controller.applySubtitleStyle)

    continue_watching = ContinueWatching(progress_repo)

    # Bars are model roles, so every surface showing progress must re-read them
    # when the underlying index moves -- whether the player advanced it or the
    # user forgot something.
    def _refresh_progress_bars() -> None:
        rows_model.refresh_progress()
        discover_model.refresh_progress()
        episode_model.refresh_progress()
        search_results_model.refresh_progress()
        search_page_model.refresh_progress()
        # Not just the bars: this row's membership changes too -- finishing or
        # forgetting a title removes it. Rebuilding is a dict read, never a
        # catalog re-fetch, which is why it is safe on the 5s playback tick.
        rows_model.set_continue_watching(continue_watching())

    progress_controller.progressChanged.connect(_refresh_progress_bars)
    # Route through ProgressController rather than wiring straight to
    # _refresh_progress_bars: the player writes progress on a path that never
    # touches this controller's own mutations, so `revision` (and anything
    # bound to it, like Detail's Forget-progress visibility) would go stale
    # even though the bars themselves refreshed fine. progressChanged above
    # already fans out to the bars, so this does not double-refresh them.
    player_controller.progressRecorded.connect(progress_controller.notifyRecorded)
    # The final seconds of a session would otherwise die with the process.
    app.aboutToQuit.connect(player_controller.flushProgress)

    ctx = engine.rootContext()
    ctx.setContextProperty("catalogController", catalog_controller)
    ctx.setContextProperty("detailController", detail_controller)
    ctx.setContextProperty("playerController", player_controller)
    ctx.setContextProperty("addonController", addon_controller)
    ctx.setContextProperty("catalogRowsModel", rows_model)
    ctx.setContextProperty("streamModel", stream_model)
    ctx.setContextProperty("episodeModel", episode_model)
    ctx.setContextProperty("discoverController", discover_controller)
    ctx.setContextProperty("discoverModel", discover_model)
    ctx.setContextProperty("discoverProxy", discover_proxy)
    ctx.setContextProperty("settingsController", settings_controller)
    ctx.setContextProperty("addonListModel", addon_list_model)
    ctx.setContextProperty("searchController", search_controller)
    ctx.setContextProperty("searchResultsModel", search_results_model)
    ctx.setContextProperty("searchPageModel", search_page_model)
    ctx.setContextProperty("progressController", progress_controller)
    ctx.setContextProperty("watchedListModel", watched_model)
    ctx.setContextProperty("deepLinkController", deep_link_controller)

    # One listener owns both delivery paths: forwarded links from a second
    # process (Linux) and QFileOpenEvent (macOS). It is created even when
    # listen() failed in main(), so the macOS path works regardless.
    deep_links = DeepLinkListener()
    deep_links.linkReceived.connect(deep_link_controller.handleLink)
    deep_links.install_macos_handler(app)

    async def bootstrap() -> None:
        # Install the default addon as protected (non-removable), restore the
        # user's persisted addons, then bring the UI up to date
        # deterministically: load the catalog rows and prime the Settings
        # addon list before bootstrap() returns.
        await install_addon(default_addon_url, protected=True)
        for url in persisted.addon_urls:
            try:
                await install_addon(url)
            except GravitasError as exc:
                # A dead addon must not block startup; it stays in the settings
                # file so a later successful launch restores it.
                addon_controller.errorOccurred.emit(f"Could not restore addon: {exc}")
        await catalog_controller.load_catalog()
        settings_controller.refreshAddons()
        # After load_catalog: set_rows() rebuilds the visible rows, so priming
        # this first would be discarded. It is a dict read, not a fetch.
        rows_model.set_continue_watching(continue_watching())
        # A link that launched the app is handled only now: installing into the
        # repository requires the repository to exist, and the confirmation
        # dialog needs a window to be centred on.
        link = pending_link(argv)
        if link is not None:
            await deep_link_controller.handleLink(link)

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
        discover_proxy,
        stream_model,
        episode_model,
        addon_list_model,
        search_results_model,
        search_page_model,
        progress_controller,
        watched_model,
        deep_link_controller,
    )
    # Same rule as the context properties: nothing else holds this, and a
    # collected listener means links silently stop arriving.
    engine._gravitas_deep_links = deep_links  # type: ignore[attr-defined]
    engine._gravitas_nam_factory = nam_factory  # type: ignore[attr-defined]
    engine._gravitas_bootstrap = bootstrap  # type: ignore[attr-defined]
    engine._gravitas_http = http  # type: ignore[attr-defined]
    return app, engine


def main() -> int:
    app = QGuiApplication(sys.argv)

    # Before anything is built: a browser launching `gravitas stremio://...`
    # starts a SECOND process while one is very likely already running. Hand the
    # link over and leave -- two instances would mean two windows fighting over
    # the same SQLite and settings files.
    link = pending_link(sys.argv)
    if link is not None and forward_to_running_instance(link):
        return 0

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    _, engine = build_app(sys.argv, DEFAULT_ADDON)
    if not engine.rootObjects():
        return 1
    listener: DeepLinkListener = engine._gravitas_deep_links  # type: ignore[attr-defined]
    # Claim the socket so the next `gravitas stremio://...` forwards here.
    # Failure is not fatal: links stop arriving from other processes, the app
    # otherwise works, and url_scheme logs why.
    listener.listen()
    with loop:
        bootstrap: Callable[[], Awaitable[None]] = engine._gravitas_bootstrap  # type: ignore[attr-defined]
        loop.create_task(bootstrap())
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
