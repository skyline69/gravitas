"""The `Gravitas` QML module: the Python objects QML reaches by name.

QML used to reach every controller and model through `setContextProperty`,
which works and which no tool can check: qmllint reports each use as an
unqualified access (700 of them), so a misspelled method or a renamed
property surfaced only as a runtime TypeError on the path that happened to
run. They are singletons of this module instead, and `scripts/build_qml_types.py`
describes them for tooling in `qml/Gravitas/gravitas.qmltypes`, so qmllint
type-checks every `DetailController.load(...)` against the Python class.

Registration is once per process, but the objects are per engine. The test
suite builds many engines in one process, and a registered *instance* is
process-global: the second app would see the first app's (by then deleted)
controllers. So each name is registered with a provider that returns what
`bind()` gave the engine asking. The runtime type is plain QObject for the
same reason -- a test may bind a stub -- and the precise types live only in
the .qmltypes, which nothing reads at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Final

import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlEngine, qmlRegisterSingletonType

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
from gravitas.presentation.controllers.window_controller import WindowController
from gravitas.presentation.models.addon_list_model import AddonListModel
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.episode_list_model import EpisodeListModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.poster_grid_proxy import PosterGridProxy
from gravitas.presentation.models.search_results_model import SearchResultsModel
from gravitas.presentation.models.stream_list_model import StreamListModel
from gravitas.presentation.models.watched_list_model import WatchedListModel

_log = logging.getLogger(__name__)

IMPORT_NAME: Final = "Gravitas"
MAJOR_VERSION: Final = 1
MINOR_VERSION: Final = 0

# Every name QML may use, and the class its object is. A class may appear
# under several names (three poster grids, two search result lists).
SINGLETONS: Final[Mapping[str, type[QObject]]] = {
    "AddonController": AddonController,
    "AddonListModel": AddonListModel,
    "CatalogController": CatalogController,
    "CatalogRowsModel": CatalogRowsModel,
    "DeepLinkController": DeepLinkController,
    "DetailController": DetailController,
    "DiscoverController": DiscoverController,
    "DiscoverModel": PosterGridModel,
    "DiscoverProxy": PosterGridProxy,
    "EpisodeModel": EpisodeListModel,
    "OnboardingController": OnboardingController,
    "PlayerController": PlayerController,
    "ProgressController": ProgressController,
    "SearchController": SearchController,
    "SearchPageModel": SearchResultsModel,
    "SearchResultsModel": SearchResultsModel,
    "SettingsController": SettingsController,
    "StreamModel": StreamListModel,
    "TraktController": TraktController,
    "WatchedListModel": WatchedListModel,
    "WatchlistController": WatchlistController,
    "WatchlistMoviesModel": PosterGridModel,
    "WatchlistSeriesModel": PosterGridModel,
    "WindowController": WindowController,
}

# Keyed by the engine's C++ address: a Python wrapper is not guaranteed to be
# the same object in the provider callback as the one bind() was handed.
_bound: dict[int, dict[str, QObject]] = {}
_registered = False
# Handed out for a name the engine was never given, so a probe test that
# builds one component does not have to bind the whole app. Reads on it are
# undefined and calls on it are TypeErrors, which is what a missing context
# property produced before.
_placeholder: QObject | None = None


def bind(engine: QQmlEngine, instances: Mapping[str, QObject]) -> None:
    """Give `engine` the objects its QML reaches by these names, adding to
    any it was given before. Call before anything the engine loads touches
    them: QML asks for each singleton once, on first use."""
    unknown = sorted(set(instances) - set(SINGLETONS))
    if unknown:
        raise KeyError(f"not names of the {IMPORT_NAME} module: {', '.join(unknown)}")
    _register_once()
    key = _address(engine)
    if key not in _bound:
        _bound[key] = {}
        engine.destroyed.connect(lambda: _bound.pop(key, None))
    _bound[key].update(instances)


def bound(engine: QQmlEngine) -> Mapping[str, QObject]:
    """What `engine` was given -- for tests that reach into a built app."""
    return _bound.get(_address(engine), {})


def _address(engine: QQmlEngine) -> int:
    return int(shiboken6.getCppPointer(engine)[0])


def _register_once() -> None:
    global _registered
    if _registered:
        return
    for name in SINGLETONS:
        # The stub types the name as bytes; the binding itself accepts only str
        # (bytes raises "called with wrong argument values").
        qmlRegisterSingletonType(  # type: ignore[call-overload]
            QObject, IMPORT_NAME, MAJOR_VERSION, MINOR_VERSION, name, _provider(name)
        )
    _registered = True


def _provider(name: str) -> Callable[..., QObject]:
    def provide(engine: QQmlEngine, *_: object) -> QObject:
        global _placeholder
        instance = _bound.get(_address(engine), {}).get(name)
        if instance is None:
            _log.debug("%s.%s requested by an engine that was not given one", IMPORT_NAME, name)
            if _placeholder is None:
                _placeholder = QObject()
            instance = _placeholder
        # The provider hands the engine an object it would otherwise own and
        # delete with itself; these belong to the composition root.
        QQmlEngine.setObjectOwnership(instance, QQmlEngine.ObjectOwnership.CppOwnership)
        return instance

    return provide
