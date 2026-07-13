import pytest

from gravitas.domain.errors import InvalidManifest
from gravitas.domain.models import CatalogRef
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
        "resources": ["catalog", {"name": "stream", "types": ["movie"]}, {"name": "meta"}],
    }
    m = parse_manifest(data, base_url="https://x/")
    assert m.resources == ("catalog", "stream", "meta")


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
