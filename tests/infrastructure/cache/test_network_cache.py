from pathlib import Path

from pytest import MonkeyPatch

from gravitas.infrastructure.cache.network_cache import (
    MAX_CACHE_BYTES,
    CachingNetworkAccessManagerFactory,
    default_network_cache_path,
)


def test_default_path_follows_xdg_cache_home(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_network_cache_path() == tmp_path / "gravitas" / "network"


def test_default_path_falls_back_to_dot_cache(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_network_cache_path() == tmp_path / ".cache" / "gravitas" / "network"


def test_path_is_cache_not_data_or_config(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    # Posters are re-downloadable, so they belong in XDG_CACHE_HOME: a user (or
    # a cleaner) deleting it loses nothing. progress.db lives in
    # XDG_DATA_HOME and settings.json in XDG_CONFIG_HOME precisely because
    # those are NOT disposable.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from gravitas.infrastructure.progress.sqlite_store import default_progress_path
    from gravitas.infrastructure.settings.json_store import default_settings_path

    assert default_network_cache_path().is_relative_to(tmp_path / "cache")
    assert not default_progress_path().is_relative_to(tmp_path / "cache")
    assert not default_settings_path().is_relative_to(tmp_path / "cache")


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
