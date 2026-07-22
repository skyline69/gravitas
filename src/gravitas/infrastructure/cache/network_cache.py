"""On-disk HTTP cache for the artwork QML loads (posters, logos, backdrops).

QML's Image fetches through Qt's own network stack, not through httpx, so
Python never sees those requests and cannot cache them itself. The supported
seam is a network access manager factory on the QML engine, which is what this
installs. QNetworkDiskCache then honours the CDN's cache headers, so artwork
that changes upstream still updates -- and it evicts by size, so the folder
cannot grow without bound.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkDiskCache
from PySide6.QtQml import QQmlNetworkAccessManagerFactory

from gravitas.infrastructure.paths import cache_dir

# Posters are ~20-60 KB each, so this holds a large library's artwork while
# staying a rounding error on any disk this app runs on.
MAX_CACHE_BYTES = 256 * 1024 * 1024


def default_network_cache_path() -> Path:
    """Artwork is re-downloadable, so it belongs in the cache dir: deleting it
    costs a user nothing but bandwidth. Contrast progress.db (the data dir)
    and settings.json (the config dir), which are not disposable."""
    return cache_dir() / "network"


class CachingNetworkAccessManagerFactory(QQmlNetworkAccessManagerFactory):
    def __init__(self, directory: Path | None = None) -> None:
        super().__init__()
        self._directory = directory if directory is not None else default_network_cache_path()

    def create(self, parent: QObject | None) -> QNetworkAccessManager:
        manager = QNetworkAccessManager(parent)
        cache = QNetworkDiskCache(manager)
        cache.setCacheDirectory(str(self._directory))
        cache.setMaximumCacheSize(MAX_CACHE_BYTES)
        # Parented to the manager, so it lives exactly as long as the manager
        # that uses it. Qt may call this once per thread; each manager gets its
        # own cache object, which is required -- a QNetworkDiskCache is not
        # safe to share across managers.
        manager.setCache(cache)
        return manager
