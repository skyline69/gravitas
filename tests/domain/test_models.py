import dataclasses

from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    ExtraSpec,
    MediaItem,
    MetaDetail,
    ResourceSpec,
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
        resources=(
            ResourceSpec(name="catalog"),
            ResourceSpec(name="meta"),
            ResourceSpec(name="stream"),
        ),
        types=("movie", "series"),
        catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
        base_url="https://x/",
    )
    assert manifest.catalogs[0].name == "Top"
    assert manifest.resource_names == ("catalog", "meta", "stream")


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
    searchable = CatalogRef(type="movie", id="s", name="S", extra=(ExtraSpec(name="search"),))
    assert searchable.supports_search is True


def test_resolved_media_constructs() -> None:
    from gravitas.domain.models import ResolvedMedia

    r = ResolvedMedia(imdb_id="tt5", type="series", name="X", poster=None, year="2020")
    assert r.imdb_id == "tt5" and r.type == "series"


def test_playback_progress_fraction() -> None:
    from gravitas.domain.models import PlaybackProgress

    def entry(**kw: object) -> PlaybackProgress:
        base: dict[str, object] = {
            "media_id": "tt1",
            "video_id": "",
            "type": "movie",
            "name": "Movie",
            "poster": None,
            "label": "",
            "position": 0.0,
            "duration": 0.0,
            "watched": False,
            "updated_at": 0,
        }
        base.update(kw)
        return PlaybackProgress(**base)  # type: ignore[arg-type]

    assert entry(position=50.0, duration=200.0).fraction == 0.25
    # Watched entries drop their position; the bar must still read full.
    assert entry(position=0.0, duration=200.0, watched=True).fraction == 1.0
    # Duration is unknown until mpv parses the file.
    assert entry(position=50.0, duration=0.0).fraction == 0.0
    # A position past a stale duration must not overflow the bar.
    assert entry(position=300.0, duration=200.0).fraction == 1.0


def _manifest(**kw: object) -> AddonManifest:
    base: dict[str, object] = {
        "id": "org.test",
        "name": "Test",
        "version": "1.0.0",
        "resources": (),
        "types": (),
        "catalogs": (),
        "base_url": "https://example.com/",
    }
    base.update(kw)
    return AddonManifest(**base)  # type: ignore[arg-type]


def test_serves_requires_the_resource_to_be_declared() -> None:
    m = _manifest(resources=(ResourceSpec(name="catalog"),))
    assert m.serves("catalog")
    assert not m.serves("stream")


def test_serves_filters_on_manifest_types() -> None:
    m = _manifest(resources=(ResourceSpec(name="stream"),), types=("movie",))
    assert m.serves("stream", "movie")
    assert not m.serves("stream", "series")


def test_serves_prefers_per_resource_types_over_manifest_types() -> None:
    # {"name": "stream", "types": ["movie"]} narrows a manifest serving both.
    m = _manifest(
        resources=(ResourceSpec(name="stream", types=("movie",)),),
        types=("movie", "series"),
    )
    assert m.serves("stream", "movie")
    assert not m.serves("stream", "series")


def test_serves_filters_on_id_prefixes() -> None:
    m = _manifest(resources=(ResourceSpec(name="meta"),), id_prefixes=("tt",))
    assert m.serves("meta", "movie", "tt0133093")
    assert not m.serves("meta", "movie", "kitsu:123")


def test_serves_prefers_per_resource_id_prefixes() -> None:
    m = _manifest(
        resources=(ResourceSpec(name="meta", id_prefixes=("kitsu:",)),),
        id_prefixes=("tt",),
    )
    assert m.serves("meta", "movie", "kitsu:123")
    assert not m.serves("meta", "movie", "tt0133093")


def test_serves_never_applies_id_prefixes_to_catalogs() -> None:
    # The protocol exempts catalogs: a catalog id is not a media id.
    m = _manifest(resources=(ResourceSpec(name="catalog"),), id_prefixes=("tt",))
    assert m.serves("catalog", "movie", "top")


def test_serves_with_no_declared_filters_accepts_anything() -> None:
    m = _manifest(resources=(ResourceSpec(name="stream"),))
    assert m.serves("stream", "series", "anything:1")


def test_stream_direct_url_is_playable() -> None:
    s = Stream(name="1080p", title="t", url="https://cdn/v.mp4", info_hash=None, file_idx=None)
    assert s.playable_url == "https://cdn/v.mp4"
    assert s.is_direct
    assert not s.is_external


def test_stream_yt_id_becomes_a_playable_youtube_url() -> None:
    s = Stream(name="Trailer", title="t", url=None, info_hash=None, file_idx=None, yt_id="abc123")
    assert s.playable_url == "https://www.youtube.com/watch?v=abc123"
    assert s.is_direct


def test_stream_info_hash_is_never_direct() -> None:
    # No torrent engine, ever (CLAUDE.md). ResolveStream filters on is_direct.
    s = Stream(name="1080p", title="t", url=None, info_hash="deadbeef", file_idx=0)
    assert s.playable_url is None
    assert not s.is_direct
    assert not s.is_external


def test_stream_external_url_is_external_not_direct() -> None:
    s = Stream(
        name="Watch",
        title="t",
        url=None,
        info_hash=None,
        file_idx=None,
        external_url="https://site/watch",
    )
    assert not s.is_direct
    assert s.is_external


def test_stream_with_both_url_and_external_url_prefers_playing_in_app() -> None:
    s = Stream(
        name="1080p",
        title="t",
        url="https://cdn/v.mp4",
        info_hash=None,
        file_idx=None,
        external_url="https://site/watch",
    )
    assert s.is_direct
    assert not s.is_external


def test_catalog_ref_derives_helpers_from_extra() -> None:
    ref = CatalogRef(
        type="movie",
        id="top",
        name="Top",
        extra=(
            ExtraSpec(name="genre", options=("Action", "Comedy")),
            ExtraSpec(name="skip"),
            ExtraSpec(name="search"),
        ),
    )
    assert ref.genres == ("Action", "Comedy")
    assert ref.supports_skip
    assert ref.supports_search
    assert ref.is_browsable


def test_catalog_ref_without_extra_is_browsable() -> None:
    ref = CatalogRef(type="movie", id="top", name="Top")
    assert ref.is_browsable
    assert not ref.supports_search
    assert ref.genres == ()


def test_catalog_requiring_genre_with_options_is_browsable() -> None:
    # Cinemeta's movie/year: we can satisfy it by passing the first option.
    ref = CatalogRef(
        type="movie",
        id="year",
        name="By year",
        extra=(ExtraSpec(name="genre", is_required=True, options=("2024", "2023")),),
    )
    assert ref.requires_genre
    assert ref.is_browsable


def test_catalog_requiring_genre_without_options_is_not_browsable() -> None:
    ref = CatalogRef(
        type="movie",
        id="year",
        name="By year",
        extra=(ExtraSpec(name="genre", is_required=True),),
    )
    assert not ref.is_browsable


def test_catalog_requiring_an_unsatisfiable_extra_is_not_browsable() -> None:
    # Cinemeta's series/last-videos wants ids from the user's library, which
    # Gravitas has no notion of; requesting it bare returns arbitrary junk.
    ref = CatalogRef(
        type="series",
        id="last-videos",
        name="Last videos",
        extra=(ExtraSpec(name="lastVideosIds", is_required=True),),
    )
    assert ref.required_extras == ("lastVideosIds",)
    assert not ref.is_browsable
