import dataclasses

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MetaDetail,
    Stream,
    Video,
)


def test_media_item_is_frozen() -> None:
    item = MediaItem(id="tt1", type="movie", name="Film", poster=None)
    assert item.name == "Film"
    with_pytest_raises = dataclasses.FrozenInstanceError
    try:
        item.name = "Other"  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except with_pytest_raises:
        pass


def test_stream_is_direct_when_url_present() -> None:
    direct = Stream(name="1080p", title="src", url="http://x/v.mkv", info_hash=None, file_idx=None)
    torrent = Stream(name="1080p", title="src", url=None, info_hash="abc", file_idx=0)
    assert direct.is_direct is True
    assert torrent.is_direct is False


def test_meta_detail_holds_episode_videos() -> None:
    meta = MetaDetail(
        id="tt2",
        type="series",
        name="Show",
        description="d",
        poster=None,
        background=None,
        videos=(Video(id="tt2:1:1", title="Pilot", season=1, episode=1),),
    )
    assert meta.videos[0].episode == 1


def test_manifest_catalogs() -> None:
    manifest = AddonManifest(
        id="a",
        name="Cinemeta",
        version="1.0",
        resources=("catalog", "meta", "stream"),
        types=("movie", "series"),
        catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
        base_url="https://x/",
    )
    assert manifest.catalogs[0].name == "Top"


def test_catalog_ref_extra_defaults() -> None:
    from gravitas.domain.models import CatalogRef

    ref = CatalogRef(type="movie", id="top", name="Top")
    assert ref.genres == ()
    assert ref.supports_skip is False


def test_meta_detail_enriched_defaults() -> None:
    meta = MetaDetail(
        id="tt1",
        type="movie",
        name="A",
        description="d",
        poster=None,
        background=None,
        videos=(),
    )
    assert meta.logo is None
    assert meta.year is None
    assert meta.runtime is None
    assert meta.imdb_rating is None
    assert meta.genres == ()
    assert meta.cast == ()
    assert meta.directors == ()


def test_meta_detail_enriched_values() -> None:
    meta = MetaDetail(
        id="tt1",
        type="movie",
        name="A",
        description="d",
        poster=None,
        background=None,
        videos=(),
        logo="l",
        year="2026",
        runtime="102 min",
        imdb_rating="7.5",
        genres=("Animation", "Comedy"),
        cast=("Tom Hanks",),
        directors=("Dir",),
    )
    assert meta.year == "2026"
    assert meta.genres == ("Animation", "Comedy")
    assert meta.directors == ("Dir",)


def test_media_item_year_defaults_none() -> None:
    item = MediaItem(id="tt1", type="movie", name="A", poster=None)
    assert item.year is None
    assert MediaItem(id="tt1", type="movie", name="A", poster=None, year="1999").year == "1999"


def test_catalog_ref_supports_search_defaults_false() -> None:
    assert CatalogRef(type="movie", id="top", name="Top").supports_search is False
    assert CatalogRef(type="movie", id="s", name="S", supports_search=True).supports_search is True


def test_resolved_media_constructs() -> None:
    from gravitas.domain.models import ResolvedMedia

    r = ResolvedMedia(imdb_id="tt5", type="series", name="X", poster=None, year="2020")
    assert r.imdb_id == "tt5" and r.type == "series"
