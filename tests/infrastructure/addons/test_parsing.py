import pytest

from gravitas.domain.errors import InvalidManifest
from gravitas.domain.models import AddonBehaviorHints, CatalogRef, ExtraSpec, ResourceSpec
from gravitas.infrastructure.addons.parsing import (
    catalog_path,
    catalog_path_extra,
    meta_path,
    parse_catalog,
    parse_manifest,
    parse_meta,
    parse_streams,
    stream_path,
)


def test_parse_manifest_maps_fields() -> None:
    data = {
        "id": "com.linvo.cinemeta",
        "name": "Cinemeta",
        "version": "3.0.0",
        "resources": ["catalog", "meta", "stream"],
        "types": ["movie", "series"],
        "catalogs": [{"type": "movie", "id": "top", "name": "Popular"}],
    }
    m = parse_manifest(data, base_url="https://v3-cinemeta.strem.io/")
    assert m.id == "com.linvo.cinemeta"
    assert m.base_url == "https://v3-cinemeta.strem.io/"
    assert m.catalogs[0] == CatalogRef(type="movie", id="top", name="Popular")


def test_parse_manifest_resource_names_both_forms() -> None:
    data = {
        "id": "x",
        "name": "X",
        "resources": [
            "catalog",
            {"name": "stream", "types": ["movie"], "idPrefixes": ["tt"]},
            {"name": "meta"},
        ],
    }
    m = parse_manifest(data, base_url="https://x/")
    assert m.resource_names == ("catalog", "stream", "meta")
    # The object form's narrowing must survive parsing, not just its name.
    assert m.resources[1] == ResourceSpec(name="stream", types=("movie",), id_prefixes=("tt",))
    assert m.resources[0] == ResourceSpec(name="catalog")


def test_parse_manifest_reads_id_prefixes() -> None:
    data = {"id": "x", "name": "X", "resources": ["meta"], "idPrefixes": ["tt", "kitsu:"]}
    m = parse_manifest(data, base_url="https://x/")
    assert m.id_prefixes == ("tt", "kitsu:")


def test_parse_manifest_reads_catalog_extra() -> None:
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {
                "type": "movie",
                "id": "year",
                "name": "By year",
                "extra": [
                    {"name": "genre", "isRequired": True, "options": ["2024"], "optionsLimit": 2},
                    {"name": "skip"},
                ],
            }
        ],
    }
    ref = parse_manifest(data, base_url="https://x/").catalogs[0]
    assert ref.extra == (
        ExtraSpec(name="genre", is_required=True, options=("2024",), options_limit=2),
        ExtraSpec(name="skip"),
    )
    assert ref.requires_genre
    assert ref.is_browsable


def test_parse_manifest_reads_legacy_extra_supported_and_required() -> None:
    # Older addons ship parallel arrays instead of `extra`.
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {
                "type": "movie",
                "id": "top",
                "name": "Top",
                "extraSupported": ["genre", "skip", "search"],
                "extraRequired": ["genre"],
                "genres": ["Action", "Comedy"],
            }
        ],
    }
    ref = parse_manifest(data, base_url="https://x/").catalogs[0]
    assert ref.genres == ("Action", "Comedy")
    assert ref.supports_skip and ref.supports_search
    assert ref.required_extras == ("genre",)


def test_parse_manifest_drops_unsupported_catalog_types() -> None:
    # channel/tv are a deliberate product decision, not a parse failure.
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {"type": "tv", "id": "iptv", "name": "IPTV"},
            {"type": "movie", "id": "top", "name": "Top"},
        ],
    }
    m = parse_manifest(data, base_url="https://x/")
    assert [c.id for c in m.catalogs] == ["top"]


def test_parse_manifest_rejects_missing_id() -> None:
    with pytest.raises(InvalidManifest):
        parse_manifest({"name": "x"}, base_url="https://x/")


def test_parse_catalog_skips_unknown_types() -> None:
    data = {
        "metas": [
            {"id": "tt1", "type": "movie", "name": "A", "poster": "http://p/1.jpg"},
            {"id": "c1", "type": "channel", "name": "skip"},
        ]
    }
    items = parse_catalog(data)
    assert len(items) == 1
    assert items[0].id == "tt1"
    assert items[0].poster == "http://p/1.jpg"


def test_parse_meta_reads_videos() -> None:
    data = {
        "meta": {
            "id": "tt2",
            "type": "series",
            "name": "Show",
            "description": "d",
            "videos": [
                {"id": "tt2:1:1", "title": "Pilot", "season": 1, "episode": 1},
            ],
        }
    }
    meta = parse_meta(data)
    assert meta.videos[0].episode == 1


def test_parse_streams_direct_and_torrent() -> None:
    data = {
        "streams": [
            {"name": "1080p", "title": "web", "url": "http://s/v.mkv"},
            {"name": "720p", "title": "torr", "infoHash": "abc", "fileIdx": 2},
        ]
    }
    streams = parse_streams(data)
    assert streams[0].is_direct is True
    assert streams[1].info_hash == "abc"
    assert streams[1].file_idx == 2


def test_parse_video_reads_episode_details() -> None:
    data = {
        "meta": {
            "id": "tt1",
            "type": "series",
            "name": "Show",
            "videos": [
                {
                    "id": "tt1:1:2",
                    "title": "Pilot II",
                    "season": 1,
                    "episode": 2,
                    "thumbnail": "http://img/ep.jpg",
                    "overview": "Things happen.",
                    "released": "2008-09-30T00:00:00.000Z",
                },
                {"id": "tt1:1:3", "name": "Bare", "season": 1, "episode": 3},
            ],
        }
    }
    videos = parse_meta(data).videos
    assert videos[0].thumbnail == "http://img/ep.jpg"
    assert videos[0].overview == "Things happen."
    assert videos[0].released == "2008-09-30"
    assert videos[1].thumbnail is None
    assert videos[1].overview is None
    assert videos[1].released is None


def test_parse_streams_cleans_display_text() -> None:
    data = {
        "streams": [
            {
                "name": "Torrentio\n4K",
                "title": "Movie.2160p.Remux\n\U0001f464 92 ⚙️‍ TG \U0001f1e9\U0001f1ea",
                "url": "http://s/v.mkv",
            }
        ]
    }
    stream = parse_streams(data)[0]
    assert stream.name == "Torrentio 4K"
    # newlines collapsed; ZWJ/variation-selector/flag chars stripped,
    # renderable emoji kept
    assert stream.title == "Movie.2160p.Remux \U0001f464 92 ⚙ TG"


def test_parse_manifest_reads_modern_extra() -> None:
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {
                "type": "movie",
                "id": "top",
                "name": "Top",
                "extra": [
                    {"name": "genre", "options": ["Action", "Comedy"]},
                    {"name": "skip"},
                ],
            }
        ],
    }
    m = parse_manifest(data, base_url="https://x/")
    ref = m.catalogs[0]
    assert ref.genres == ("Action", "Comedy")
    assert ref.supports_skip is True


def test_parse_manifest_reads_legacy_extra() -> None:
    data = {
        "id": "x",
        "name": "X",
        "catalogs": [
            {
                "type": "movie",
                "id": "top",
                "name": "Top",
                "extraSupported": ["genre", "skip"],
                "genres": ["Drama"],
            }
        ],
    }
    ref = parse_manifest(data, base_url="https://x/").catalogs[0]
    assert ref.genres == ("Drama",)
    assert ref.supports_skip is True


def test_parse_manifest_extra_absent_defaults() -> None:
    data = {"id": "x", "name": "X", "catalogs": [{"type": "movie", "id": "top", "name": "Top"}]}
    ref = parse_manifest(data, base_url="https://x/").catalogs[0]
    assert ref.genres == ()
    assert ref.supports_skip is False


def test_paths() -> None:
    assert catalog_path(CatalogRef(type="movie", id="top", name="T")) == "catalog/movie/top.json"
    assert meta_path("series", "tt2") == "meta/series/tt2.json"
    assert stream_path("movie", "tt1") == "stream/movie/tt1.json"


def test_catalog_path_extra() -> None:
    ref = CatalogRef(type="movie", id="top", name="T")
    assert catalog_path_extra(ref, None, 0) == "catalog/movie/top.json"
    assert catalog_path_extra(ref, "Action", 0) == "catalog/movie/top/genre=Action.json"
    assert catalog_path_extra(ref, None, 100) == "catalog/movie/top/skip=100.json"
    assert catalog_path_extra(ref, "Action", 100) == "catalog/movie/top/genre=Action&skip=100.json"
    assert (
        catalog_path_extra(ref, "Sci-Fi & Fantasy", 0)
        == "catalog/movie/top/genre=Sci-Fi%20%26%20Fantasy.json"
    )
    # a "/" in a genre must be percent-encoded, not injected as a path separator
    assert (
        catalog_path_extra(ref, "Action/Adventure", 0)
        == "catalog/movie/top/genre=Action%2FAdventure.json"
    )


def test_parse_meta_reads_enriched_fields() -> None:
    data = {
        "meta": {
            "id": "tt1",
            "type": "movie",
            "name": "Toy Story 5",
            "description": "desc",
            "logo": "http://l/logo.png",
            "background": "http://b/bg.jpg",
            "releaseInfo": "2026",
            "runtime": "102 min",
            "imdbRating": "7.5",
            "genres": ["Animation", "Comedy", 3],
            "cast": ["Tom Hanks", "Tim Allen"],
            "director": ["Andrew Stanton"],
        }
    }
    m = parse_meta(data)
    assert m.logo == "http://l/logo.png"
    assert m.year == "2026"
    assert m.runtime == "102 min"
    assert m.imdb_rating == "7.5"
    assert m.genres == ("Animation", "Comedy")  # non-string 3 skipped
    assert m.cast == ("Tom Hanks", "Tim Allen")
    assert m.directors == ("Andrew Stanton",)


def test_parse_meta_year_falls_back_to_year_field() -> None:
    data = {"meta": {"id": "tt1", "type": "movie", "name": "A", "year": "1999"}}
    assert parse_meta(data).year == "1999"


def test_parse_meta_missing_enriched_fields_default() -> None:
    data = {"meta": {"id": "tt1", "type": "movie", "name": "A"}}
    m = parse_meta(data)
    assert m.logo is None and m.year is None and m.runtime is None
    assert m.imdb_rating is None and m.genres == () and m.cast == () and m.directors == ()


def test_parse_manifest_marks_supports_search() -> None:
    data = {
        "id": "c",
        "name": "C",
        "version": "1",
        "types": ["movie"],
        "resources": ["catalog"],
        "catalogs": [
            {"type": "movie", "id": "top", "name": "Top", "extraSupported": ["search", "skip"]},
            {"type": "movie", "id": "plain", "name": "Plain"},
        ],
    }
    m = parse_manifest(data, base_url="https://x/")
    assert m.catalogs[0].supports_search is True
    assert m.catalogs[1].supports_search is False


def test_parse_manifest_supports_search_legacy_extra() -> None:
    data = {
        "id": "c",
        "name": "C",
        "version": "1",
        "types": ["movie"],
        "resources": ["catalog"],
        "catalogs": [
            {
                "type": "movie",
                "id": "s",
                "name": "S",
                "extra": [{"name": "search", "isRequired": True}],
            },
        ],
    }
    assert parse_manifest(data, base_url="https://x/").catalogs[0].supports_search is True


def test_catalog_path_extra_search() -> None:
    ref = CatalogRef(type="movie", id="top", name="Top")
    expected = "catalog/movie/top/search=the%20matrix.json"
    assert catalog_path_extra(ref, None, 0, "the matrix") == expected
    # combined with skip
    expected_skip = "catalog/movie/top/skip=20&search=x.json"
    assert catalog_path_extra(ref, None, 20, "x") == expected_skip


def test_parse_catalog_extracts_year_and_rating() -> None:
    from gravitas.infrastructure.addons.parsing import parse_catalog

    data = {
        "metas": [
            {
                "id": "tt1",
                "type": "series",
                "name": "The Boys",
                "releaseInfo": "2019-2026",
                "imdbRating": "8.7",
            }
        ]
    }
    item = parse_catalog(data)[0]
    assert item.year == "2019"
    assert item.imdb_rating == "8.7"


def test_parse_streams_prefers_description_over_deprecated_title() -> None:
    # The SDK deprecates `title` in favour of `description`.
    data = {"streams": [{"name": "1080p", "description": "5.1 · 2.1GB", "url": "https://c/v.mp4"}]}
    assert parse_streams(data)[0].title == "5.1 · 2.1GB"


def test_parse_streams_falls_back_title_then_name() -> None:
    assert parse_streams({"streams": [{"name": "n", "title": "t"}]})[0].title == "t"
    assert parse_streams({"streams": [{"name": "n"}]})[0].title == "n"


def test_parse_streams_reads_yt_id_and_external_url() -> None:
    data = {"streams": [{"name": "YT", "ytId": "abc"}, {"name": "Web", "externalUrl": "https://s"}]}
    streams = parse_streams(data)
    assert streams[0].yt_id == "abc"
    assert streams[0].is_direct
    assert streams[1].external_url == "https://s"
    assert streams[1].is_external


def test_parse_streams_reads_proxy_headers() -> None:
    data = {
        "streams": [
            {
                "name": "1080p",
                "url": "https://c/v.mp4",
                "behaviorHints": {
                    "notWebReady": True,
                    "proxyHeaders": {
                        "request": {"Referer": "https://origin/", "User-Agent": "Mozilla/5.0"},
                        "response": {"Ignored": "yes"},
                    },
                },
            }
        ]
    }
    assert parse_streams(data)[0].proxy_headers == (
        ("Referer", "https://origin/"),
        ("User-Agent", "Mozilla/5.0"),
    )


def test_parse_streams_without_proxy_headers_is_empty() -> None:
    assert parse_streams({"streams": [{"name": "n", "url": "u"}]})[0].proxy_headers == ()
    malformed = {"streams": [{"name": "n", "url": "u", "behaviorHints": {"proxyHeaders": "nope"}}]}
    assert parse_streams(malformed)[0].proxy_headers == ()


def test_parse_meta_prefers_legacy_arrays_over_links() -> None:
    # Cinemeta sends both; the flat arrays stay authoritative.
    data = {
        "meta": {
            "id": "tt1",
            "type": "movie",
            "name": "M",
            "genres": ["Action"],
            "cast": ["Keanu Reeves"],
            "director": ["Lana Wachowski"],
            "writer": ["Lilly Wachowski"],
            "links": [{"name": "Ignored", "category": "Cast", "url": "u"}],
        }
    }
    meta = parse_meta(data)
    assert meta.cast == ("Keanu Reeves",)
    assert meta.genres == ("Action",)
    assert meta.directors == ("Lana Wachowski",)
    assert meta.writers == ("Lilly Wachowski",)


def test_parse_meta_derives_people_from_links_when_arrays_absent() -> None:
    data = {
        "meta": {
            "id": "tt1",
            "type": "movie",
            "name": "M",
            "links": [
                {"name": "8.7", "category": "imdb", "url": "https://imdb.com/title/tt1"},
                {"name": "Action", "category": "Genres", "url": "u"},
                {"name": "Sci-Fi", "category": "Genres", "url": "u"},
                {"name": "Keanu Reeves", "category": "Cast", "url": "u"},
                {"name": "Lana Wachowski", "category": "Directors", "url": "u"},
                {"name": "Lilly Wachowski", "category": "Writers", "url": "u"},
            ],
        }
    }
    meta = parse_meta(data)
    assert meta.genres == ("Action", "Sci-Fi")
    assert meta.cast == ("Keanu Reeves",)
    assert meta.directors == ("Lana Wachowski",)
    assert meta.writers == ("Lilly Wachowski",)


def test_parse_meta_reads_trailer_and_default_video_id() -> None:
    data = {
        "meta": {
            "id": "tt0133093",
            "type": "movie",
            "name": "The Matrix",
            "trailerStreams": [{"title": "The Matrix", "ytId": "FVI84Dfx2-I"}],
            "trailers": [{"source": "legacy", "type": "Trailer"}],
            "behaviorHints": {"defaultVideoId": "tt0133093", "hasScheduledVideos": False},
        }
    }
    meta = parse_meta(data)
    assert meta.trailer_yt_id == "FVI84Dfx2-I"
    assert meta.default_video_id == "tt0133093"


def test_parse_meta_falls_back_to_legacy_trailers() -> None:
    data = {
        "meta": {
            "id": "tt1",
            "type": "movie",
            "name": "M",
            "trailers": [{"source": "abc123", "type": "Trailer"}],
        }
    }
    assert parse_meta(data).trailer_yt_id == "abc123"


def test_parse_meta_null_default_video_id() -> None:
    # Cinemeta sends behaviorHints.defaultVideoId: null for series.
    data = {
        "meta": {
            "id": "tt1",
            "type": "series",
            "name": "S",
            "behaviorHints": {"defaultVideoId": None, "hasScheduledVideos": True},
        }
    }
    assert parse_meta(data).default_video_id is None


def test_poster_shape_defaults_and_reads() -> None:
    catalog = {
        "metas": [
            {"id": "a", "type": "movie", "name": "A"},
            {"id": "b", "type": "movie", "name": "B", "posterShape": "landscape"},
            {"id": "c", "type": "movie", "name": "C", "posterShape": "bogus"},
        ]
    }
    items = parse_catalog(catalog)
    assert items[0].poster_shape == "poster"
    assert items[1].poster_shape == "landscape"
    # An unknown shape must not leak an invalid value into QML.
    assert items[2].poster_shape == "poster"
    meta = parse_meta({"meta": {"id": "a", "type": "movie", "name": "A", "posterShape": "square"}})
    assert meta.poster_shape == "square"


def test_catalog_path_extra_supplies_a_required_genre() -> None:
    ref = CatalogRef(
        type="movie",
        id="year",
        name="By year",
        extra=(ExtraSpec(name="genre", is_required=True, options=("2024", "2023")),),
    )
    # Bare request must still carry a genre; the addon's first option is it.
    assert catalog_path_extra(ref, None, 0) == "catalog/movie/year/genre=2024.json"
    # An explicit pick always wins.
    assert catalog_path_extra(ref, "2023", 0) == "catalog/movie/year/genre=2023.json"


def test_catalog_path_extra_does_not_invent_a_genre_when_optional() -> None:
    ref = CatalogRef(type="movie", id="top", name="Top", extra=(ExtraSpec(name="genre"),))
    assert catalog_path_extra(ref, None, 0) == "catalog/movie/top.json"


def test_parse_manifest_reads_behavior_hints() -> None:
    data = {
        "id": "x",
        "name": "X",
        "description": "Streams things",
        "logo": "https://x/logo.png",
        "behaviorHints": {
            "adult": True,
            "p2p": True,
            "configurable": True,
            "configurationRequired": True,
        },
    }
    m = parse_manifest(data, base_url="https://x/")
    assert m.description == "Streams things"
    assert m.logo == "https://x/logo.png"
    assert m.behavior_hints == AddonBehaviorHints(
        adult=True, p2p=True, configurable=True, configuration_required=True
    )
    assert m.configure_url == "https://x/configure"


def test_parse_manifest_behavior_hints_default_false() -> None:
    m = parse_manifest({"id": "x", "name": "X"}, base_url="https://x/")
    assert m.behavior_hints == AddonBehaviorHints()
    assert m.description is None


def test_parse_manifest_tolerates_malformed_behavior_hints() -> None:
    m = parse_manifest({"id": "x", "name": "X", "behaviorHints": "nope"}, base_url="https://x/")
    assert m.behavior_hints == AddonBehaviorHints()


def test_configure_url_when_manifest_url_had_no_trailing_slash() -> None:
    # base_url is normalised by parse_manifest, so configure_url must not
    # produce a doubled or missing slash.
    m = parse_manifest({"id": "x", "name": "X"}, base_url="https://x")
    assert m.configure_url == "https://x/configure"
