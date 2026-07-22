from pathlib import Path

from gravitas.infrastructure.cache.network_cache import (
    MAX_CACHE_BYTES,
    CachingNetworkAccessManagerFactory,
    default_network_cache_path,
)
from gravitas.infrastructure.paths import cache_dir


def test_default_path_lives_in_the_cache_dir() -> None:
    # Which directory that is per platform is paths.py's business (and its
    # tests'); here it only matters that artwork goes to the disposable one.
    assert default_network_cache_path() == cache_dir() / "network"


def test_path_is_cache_not_data_or_config() -> None:
    # Posters are re-downloadable, so they belong in the cache dir: a user (or
    # a cleaner) wiping it loses nothing. progress.db and settings.json are NOT
    # disposable, so nothing of theirs may sit under it.
    from gravitas.infrastructure.progress.sqlite_store import default_progress_path
    from gravitas.infrastructure.settings.json_store import default_settings_path

    assert default_network_cache_path().is_relative_to(cache_dir())
    assert not default_progress_path().is_relative_to(cache_dir())
    assert not default_settings_path().is_relative_to(cache_dir())


def test_factory_creates_a_manager_with_a_disk_cache(qapp: object, tmp_path: Path) -> None:
    factory = CachingNetworkAccessManagerFactory(tmp_path / "net")
    manager = factory.create(None)
    cache = manager.cache()
    assert cache is not None, "no cache attached: posters would re-download every launch"
    assert Path(cache.cacheDirectory()) == tmp_path / "net"
    assert cache.maximumCacheSize() == MAX_CACHE_BYTES


def test_each_manager_gets_its_own_cache_object(qapp: object, tmp_path: Path) -> None:
    # Qt may call create() per thread; sharing one QNetworkDiskCache across
    # managers is not safe.
    factory = CachingNetworkAccessManagerFactory(tmp_path / "net")
    first = factory.create(None)
    second = factory.create(None)
    assert first.cache() is not second.cache()
    # ...but they must agree on where the cache lives, or one of them is
    # writing somewhere nobody reads.
    assert first.cache().cacheDirectory() == second.cache().cacheDirectory()


def test_cache_survives_a_manager_being_collected(qapp: object, tmp_path: Path) -> None:
    # The QNetworkDiskCache is parented to its manager; if Python drops the
    # only reference the cache dies and every request silently misses.
    factory = CachingNetworkAccessManagerFactory(tmp_path / "net")
    manager = factory.create(None)
    import gc

    gc.collect()
    assert manager.cache() is not None
    assert manager.cache().maximumCacheSize() == MAX_CACHE_BYTES
