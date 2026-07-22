"""Composition root: wire adapters into use cases and launch the QML app."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import qasync  # type: ignore[import-untyped]
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QMessageLogContext,
    QtMsgType,
    qInstallMessageHandler,
)
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication, QIcon, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle

from gravitas.application.addon_repository import AddonRepository
from gravitas.application.browse_board import BrowseBoard
from gravitas.application.browse_catalog import BrowseCatalog
from gravitas.application.continue_watching import ContinueWatching
from gravitas.application.get_detail import GetDetail
from gravitas.application.get_ratings import GetRatings
from gravitas.application.install_addon import InstallAddon
from gravitas.application.preview_addon import PreviewAddon
from gravitas.application.resolve_media_link import ResolveMediaLink
from gravitas.application.resolve_stream import ResolveStream
from gravitas.application.search_media import SearchMedia
from gravitas.application.trakt_account import TraktAccount
from gravitas.application.trakt_rows import TraktRows, rows_from_payload, rows_to_payload
from gravitas.application.trakt_sync import TraktSync
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.application.watch_progress import WatchProgressRepository
from gravitas.application.watchlist import WatchlistRepository
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import SubtitleStyle
from gravitas.domain.ports import MediaPlayer
from gravitas.infrastructure.addons.client import AddonClient
from gravitas.infrastructure.cache.json_disk_cache import JsonDiskCache
from gravitas.infrastructure.cache.network_cache import CachingNetworkAccessManagerFactory
from gravitas.infrastructure.desktop.url_scheme import (
    DeepLinkListener,
    forward_to_running_instance,
)
from gravitas.infrastructure.metadata.mdblist_resolver import MdbListResolver
from gravitas.infrastructure.metadata.tmdb_resolver import TmdbResolver
from gravitas.infrastructure.player.mpv_player import MpvPlayer
from gravitas.infrastructure.progress.sqlite_store import SqliteProgressStore
from gravitas.infrastructure.settings.json_store import JsonSettingsStore
from gravitas.infrastructure.trakt import app_credentials as trakt_app
from gravitas.infrastructure.trakt.client import TraktClient
from gravitas.infrastructure.watchlist.sqlite_store import SqliteWatchlistStore
from gravitas.logging_setup import configure_logging
from gravitas.presentation.controllers.addon_controller import AddonController
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.controllers.deep_link_controller import DeepLinkController
from gravitas.presentation.controllers.detail_controller import DetailController
from gravitas.presentation.controllers.discover_controller import DiscoverController
from gravitas.presentation.controllers.onboarding_controller import OnboardingController
from gravitas.presentation.controllers.player_controller import PlayerController
from gravitas.presentation.controllers.progress_controller import ProgressController
from gravitas.presentation.controllers.search_controller import SearchController
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.controllers.trakt_controller import TraktController
from gravitas.presentation.controllers.watchlist_controller import WatchlistController
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
# Disk-cache key for the last shown Trakt rows (not a real URL — the cache is
# keyed by arbitrary strings and this one cannot collide with addon URLs).
_TRAKT_ROWS_KEY = "gravitas://trakt-rows"


# Qt runtime warnings we deliberately swallow. An addon's `/meta` routinely
# points at CDN artwork that has since 404'd (or times out); Qt's `Image`
# already degrades gracefully -- it holds the skeleton and never fades the
# poster in -- so the per-poster `Error transferring ...` warning is pure
# noise with no action attached. (Matched without the emitter prefix: Qt 6.11
# renamed it from `QQuickImage:` to `QML QQuickImage*:`, which silently
# un-matched the old needle.)
#
# `QIODevice::read (QSslSocket): device not open` is the tail of the same
# story: a poster fetch aborted because its delegate was torn down mid-flight
# (a model reset while images stream in). The transfer was for a card that no
# longer exists; nothing to act on.
#
# The bundled Inter.ttf carries only Latin/Cyrillic/Greek OpenType tables, so
# any addon title in Devanagari, Arabic, CJK, etc. makes Qt log
# `OpenType support missing for "Inter", script N` before it transparently
# falls back to a system font that covers the script. The glyphs still render;
# the warning is noise with no action attached.
# We set a Wayland app_id (setDesktopFileName) so installed builds group under
# their .desktop icon. In an uninstalled dev run no such .desktop exists, so the
# xdg-desktop-portal logs `Could not register app ID ... App info not found`.
# KWin still uses the window icon we set, and installed builds ship the desktop
# file, so this line is noise only in the dev checkout.
_MUTED_QT_WARNINGS = (
    "Error transferring",
    "QIODevice::read (QSslSocket): device not open",
    "OpenType support missing",
    "Could not register app ID",
)


def _install_qt_log_filter() -> None:
    """Drop known-benign Qt warnings, forward everything else into logging.

    Qt routes *all* its output -- including QML runtime warnings like the image
    loader's -- through one message handler, so this is the only place that can
    filter them. Non-muted lines are re-emitted through the `qt` logger (its
    category as a child logger), so Qt output shares the app's colored format
    instead of landing as bare stderr prints.
    """
    qt_levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode: QtMsgType, context: QMessageLogContext, message: str) -> None:
        if mode == QtMsgType.QtWarningMsg and any(
            needle in message for needle in _MUTED_QT_WARNINGS
        ):
            return
        name = "qt" if context.category in (None, "default") else f"qt.{context.category}"
        logging.getLogger(name).log(qt_levels.get(mode, logging.INFO), "%s", message)

    qInstallMessageHandler(handler)


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

    # App identity + taskbar/window icon. setDesktopFileName sets the Wayland
    # xdg app_id, which is how a compositor maps the window to the installed
    # .desktop entry for its icon; setWindowIcon covers X11 and compositors that
    # honour the xdg-toplevel icon directly, so a raw `uv run gravitas` shows
    # the real icon instead of the generic Wayland fallback.
    app.setApplicationName("Gravitas")
    app.setApplicationDisplayName("Gravitas")
    app.setDesktopFileName("dev.skyline.Gravitas")
    _icon = _QML_DIR / "assets" / "gravitas.png"
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))

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

    # HTTP/2 so the cross-addon catalog fan-out multiplexes its many parallel
    # GETs over one connection per host instead of racing the keepalive pool.
    http = httpx.AsyncClient(http2=True)
    # Persistent JSON cache under the in-memory one: a warm launch paints the
    # grid from disk instead of refetching every catalog. Pruned here (a
    # bounded delete) so the file cannot grow forever.
    disk_cache = JsonDiskCache()
    disk_cache.prune()
    source = AddonClient(http, disk=disk_cache)
    repo = AddonRepository(source)

    class _KeyHolder:
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

    tmdb_key = _KeyHolder()
    tmdb_key.key = persisted.tmdb_key
    tmdb_resolver = TmdbResolver(http, lambda: tmdb_key.key)

    mdblist_key = _KeyHolder()
    mdblist_key.key = persisted.mdblist_key
    mdblist_resolver = MdbListResolver(http, lambda: mdblist_key.key)
    get_ratings = GetRatings(mdblist_resolver)

    class _SubStyleHolder:
        style: SubtitleStyle = SubtitleStyle()

    sub_style = _SubStyleHolder()
    sub_style.style = persisted.subtitle_style

    trakt_account = TraktAccount(TraktClient(http))
    # The app ships its own Trakt credentials (Stremio-style one-click auth);
    # only the granted session is user state.
    trakt_account.client_id = trakt_app.CLIENT_ID or None
    trakt_account.client_secret = trakt_app.CLIENT_SECRET or None
    trakt_account.auth = persisted.trakt_auth
    trakt_account.sync_forgets = persisted.trakt_sync_forgets
    trakt_account.sync_watched = persisted.trakt_sync_watched

    rows_model = CatalogRowsModel(progress_repo)
    stream_model = StreamListModel()

    discover_model = PosterGridModel(progress_repo)
    discover_proxy = PosterGridProxy(discover_model)
    discover_controller = DiscoverController(BrowseBoard(repo), repo, discover_model)

    catalog_controller = CatalogController(BrowseCatalog(repo), rows_model)
    episode_model = EpisodeListModel(progress_repo)
    detail_controller = DetailController(
        GetDetail(repo),
        ResolveStream(repo),
        stream_model,
        episode_model,
        progress_repo,
        get_ratings=get_ratings,
    )
    install_addon = InstallAddon(repo)
    addon_controller = AddonController(install_addon, catalog_controller)
    deep_link_controller = DeepLinkController(PreviewAddon(source), addon_controller)
    addon_list_model = AddonListModel()

    class _OnboardingHolder:
        done: bool = False

    onboarding = _OnboardingHolder()
    onboarding.done = persisted.onboarding_done

    settings_controller = SettingsController(
        UninstallAddon(repo),
        repo,
        addon_list_model,
        catalog_controller,
        tmdb_key,
        settings_store,
        sub_style,
        mdblist_key,
        trakt_account,
        onboarding,
    )
    onboarding_controller = OnboardingController(onboarding, settings_controller.persist)
    trakt_sync = TraktSync(trakt_account, progress_repo, GetDetail(repo))
    trakt_controller = TraktController(
        trakt_account,
        settings_controller.persist,
        trakt_sync,
        TraktRows(trakt_account, GetDetail(repo)),
        rows_model,
        # Snapshot the rows just shown so the next boot paints them
        # instantly; the payload is small, so the write is a non-event.
        rows_persist=lambda rows: disk_cache.put(_TRAKT_ROWS_KEY, rows_to_payload(rows)),
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

    watchlist_repo = WatchlistRepository(SqliteWatchlistStore())
    # One model per Watchlist section (Movies / Series), progress-aware so
    # watchlist posters carry the same bars/checkmarks as every other grid.
    watchlist_movies_model = PosterGridModel(progress_repo)
    watchlist_series_model = PosterGridModel(progress_repo)
    watchlist_controller = WatchlistController(
        watchlist_repo, watchlist_movies_model, watchlist_series_model
    )

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
        watchlist_movies_model.refresh_progress()
        watchlist_series_model.refresh_progress()
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
    # Scrobble what plays. The asyncSlot schedules onto the qasync loop; with
    # no Trakt session connected every event is a cheap no-op.
    player_controller.scrobbleEvent.connect(trakt_controller.onScrobbleEvent)
    # A pull from Trakt mutates the progress repo behind ProgressController's
    # back — same staleness problem as the player's writes, same cure.
    trakt_controller.syncCompleted.connect(lambda _applied: progress_controller.notifyRecorded())
    # Forgetting locally also drops Trakt's paused row, or the next sync
    # would resurrect exactly what the user just deleted.
    progress_controller.progressForgotten.connect(trakt_controller.onProgressForgotten)
    progress_controller.mediaForgotten.connect(trakt_controller.onMediaForgotten)
    progress_controller.allProgressReset.connect(trakt_controller.onAllProgressReset)
    # Marking watched locally also lands in Trakt's history, so every Trakt
    # client agrees on what is finished.
    progress_controller.watchedMarked.connect(trakt_controller.onWatchedMarked)
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
    ctx.setContextProperty("traktController", trakt_controller)
    ctx.setContextProperty("watchlistController", watchlist_controller)
    ctx.setContextProperty("watchlistMoviesModel", watchlist_movies_model)
    ctx.setContextProperty("watchlistSeriesModel", watchlist_series_model)
    ctx.setContextProperty("deepLinkController", deep_link_controller)
    ctx.setContextProperty("onboardingController", onboarding_controller)

    # One listener owns both delivery paths: forwarded links from a second
    # process (Linux) and QFileOpenEvent (macOS). It is created even when
    # listen() failed in main(), so the macOS path works regardless.
    deep_links = DeepLinkListener()
    deep_links.linkReceived.connect(deep_link_controller.handleLink)
    deep_links.install_macos_handler(app)

    async def _install_and_load() -> None:
        # Install the default addon as protected (non-removable), restore the
        # user's persisted addons, then load the catalog rows. Quiet: pass
        # one runs behind the boot overlay, pass two behind live content.
        await install_addon(default_addon_url, protected=True)
        for url in persisted.addon_urls:
            try:
                await install_addon(url)
            except GravitasError as exc:
                # A dead addon must not block startup; it stays in the
                # settings file so a later successful launch restores it.
                addon_controller.errorOccurred.emit(f"Could not restore addon: {exc}")
        await catalog_controller.load_catalog(quiet=True)

    async def bootstrap() -> None:
        # Stale-while-revalidate, in two passes.
        #
        # Pass one runs behind the boot gate with the addon client serving
        # disk-cached JSON of any age: on a warm start the whole grid —
        # catalog, Continue Watching, the last session's Trakt rows — builds
        # without touching the network, and the spinner lasts a blink. Cold
        # caches degrade to exactly the old behaviour (fetch behind the
        # spinner).
        #
        # Pass two repeats the load with staleness honoured and pulls Trakt,
        # AFTER the reveal. Its writes are surgical: set_rows() skips when
        # the refreshed catalog is unchanged (the common case), and Trakt
        # rows splice in without touching the catalog rows' delegates.
        catalog_controller.set_booting(True)
        source.serve_stale = True
        try:
            await _install_and_load()
            settings_controller.refreshAddons()
            # After load_catalog: set_rows() rebuilds the visible rows, so
            # priming this first would be discarded. A dict read, not a fetch.
            rows_model.set_continue_watching(continue_watching())
            if trakt_account.authenticated:
                snapshot = await asyncio.to_thread(disk_cache.get, _TRAKT_ROWS_KEY)
                if snapshot is not None:
                    rows_model.set_trakt_rows(rows_from_payload(snapshot[0]))
            # A settle beat before the reveal: the grid sits invisible in the
            # scene, so this hands the event loop ~25 frames to incubate
            # delegates and decode the first posters while the spinner still
            # owns the screen. Dropping the gate on the same frame the data
            # landed pushed all of that into the reveal fade, and the
            # spinner's final moments visibly stuttered.
            await asyncio.sleep(0.4)
        finally:
            # The gate must fall whatever happened above — a dead network
            # shows an empty grid with toasts, never an eternal spinner.
            source.serve_stale = False
            catalog_controller.set_booting(False)
        # A link that launched the app is handled only now: installing into the
        # repository requires the repository to exist, and the confirmation
        # dialog needs a window to be centred on. Before pass two, so a slow
        # revalidation never delays the link the user launched us with.
        link = pending_link(argv)
        if link is not None:
            await deep_link_controller.handleLink(link)
        # Pass two: revalidate.
        await _install_and_load()
        settings_controller.refreshAddons()
        if trakt_account.authenticated:
            await trakt_controller.sync_quietly()
            await trakt_controller.refresh_rows_quietly()

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
        trakt_controller,
        watchlist_controller,
        watchlist_movies_model,
        watchlist_series_model,
        deep_link_controller,
        onboarding_controller,
    )
    # Same rule as the context properties: nothing else holds this, and a
    # collected listener means links silently stop arriving.
    engine._gravitas_deep_links = deep_links  # type: ignore[attr-defined]
    engine._gravitas_nam_factory = nam_factory  # type: ignore[attr-defined]
    engine._gravitas_bootstrap = bootstrap  # type: ignore[attr-defined]
    engine._gravitas_http = http  # type: ignore[attr-defined]
    return app, engine


def main() -> int:
    # We force the OpenGL scene-graph backend (the in-scene mpv renderer needs
    # it), so Qt inits EGL. Where the NVIDIA blob and mesa coexist under
    # Wayland, mesa's libEGL gets handed the NVIDIA DRM node, can't drive it,
    # and prints `failed to create dri2 screen` warnings before Qt falls back
    # to the working NVIDIA path anyway. Raise libEGL's log threshold to hush
    # that dead-end probe; rendering is unaffected. A user-set value wins.
    os.environ.setdefault("EGL_LOG_LEVEL", "fatal")

    configure_logging()
    _install_qt_log_filter()

    # macOS hands out a legacy OpenGL 2.1 compatibility context unless a core
    # profile is requested explicitly, and mpv's GPU renderer then degrades:
    # lanczos/hermite scalers disabled ("GLSL version too old") and the
    # videotoolbox hwdec interop path refused ("need >= OpenGL 3.0 for core
    # rectangle texture support"). 3.2 core is the floor of what macOS offers
    # beyond 2.1 and Qt Quick's RHI is core-profile safe. Must be set before
    # the QGuiApplication exists. Left untouched elsewhere: Linux/Windows
    # already get modern compatibility contexts where none of this bites.
    if sys.platform == "darwin":
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 2)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        QSurfaceFormat.setDefaultFormat(fmt)

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
        # run_forever() returns once the app is quitting. Destroy the QML scene
        # HERE — still inside `with loop` (loop open) and before this function
        # returns (context-property objects still referenced). Otherwise the
        # scene is torn down later, after the loop closes and during interpreter
        # GC, where teardown re-evaluations hit a closed loop (an `onAtYEndChanged`
        # → loadMore asyncSlot does ensure_future → "Event loop is closed") or a
        # freed context object ("TypeError: Cannot read property 'count' of null").
        # Deferred-delete must be flushed synchronously: the loop is no longer
        # running, so nothing else will process the posted delete events.
        for obj in engine.rootObjects():
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
