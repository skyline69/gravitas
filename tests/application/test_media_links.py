import pytest

from gravitas.application.media_links import parse_media_link


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.imdb.com/title/tt0133093/", ("imdb", "tt0133093")),
        ("tt0133093", ("imdb", "tt0133093")),
        ("  tt1234567  ", ("imdb", "tt1234567")),
        ("https://thetvdb.com/dereferrer/series/81189", ("tvdb", "81189")),
        ("https://www.thetvdb.com/?tab=series&id=81189", ("tvdb", "81189")),
        ("tvdb:81189", ("tvdb", "81189")),
        ("the matrix", None),
        ("https://thetvdb.com/series/breaking-bad", None),  # slug, no numeric id
        ("", None),
    ],
)
def test_parse_media_link(text: str, expected: object) -> None:
    assert parse_media_link(text) == expected
