from gravitas.application.search_ranking import rank
from gravitas.domain.models import MediaItem


def _item(id_: str, name: str, rating: str | None = None) -> MediaItem:
    return MediaItem(id=id_, type="series", name=name, poster=None, imdb_rating=rating)


def test_rank_by_match_tier_then_rating() -> None:
    items = [
        _item("1", "House of the Dragon"),  # no match -> tier 4
        _item("2", "The Boys", "8.7"),  # exact -> tier 0
        _item("3", "The Boys Presents", "6.8"),  # startswith -> tier 1
        _item("4", "The Boys", "5.7"),  # exact, lower rating
    ]
    assert [i.id for i in rank("The Boys", items)] == ["2", "4", "3", "1"]


def test_rank_empty_query_is_noop() -> None:
    items = [_item("1", "B"), _item("2", "A")]
    assert rank("  ", items) == items
