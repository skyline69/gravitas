from gravitas.domain.models import MediaItem
from gravitas.presentation.models.poster_grid_model import PosterGridModel
from gravitas.presentation.models.poster_grid_proxy import PosterGridProxy


def _item(
    id_: str,
    name: str,
    year: str | None = None,
    rating: str | None = None,
) -> MediaItem:
    return MediaItem(id=id_, type="movie", name=name, poster=None, year=year, imdb_rating=rating)


def _names(proxy: PosterGridProxy) -> list[str]:
    return [
        str(proxy.data(proxy.index(row, 0), PosterGridModel.NameRole))
        for row in range(proxy.rowCount())
    ]


def _build(items: list[MediaItem]) -> tuple[PosterGridModel, PosterGridProxy]:
    model = PosterGridModel()
    model.set_items(items)
    return model, PosterGridProxy(model)


def test_filter_narrows_case_insensitively(qapp: object) -> None:
    _, proxy = _build([_item("1", "Alien"), _item("2", "Aliens"), _item("3", "Blade")])
    proxy.setFilterText("alien")
    assert _names(proxy) == ["Alien", "Aliens"]
    proxy.setFilterText("")
    assert _names(proxy) == ["Alien", "Aliens", "Blade"]


def test_count_tracks_filter_and_total(qapp: object) -> None:
    model, proxy = _build([_item("1", "Alien"), _item("2", "Blade")])
    assert proxy.count == 2
    assert proxy.totalCount == 2
    proxy.setFilterText("alien")
    assert proxy.count == 1
    assert proxy.totalCount == 2
    model.append_items([_item("3", "Alien 3")])
    assert proxy.count == 2
    assert proxy.totalCount == 3


def test_sort_by_name_is_case_insensitive(qapp: object) -> None:
    _, proxy = _build([_item("1", "blade"), _item("2", "Alien"), _item("3", "Coma")])
    proxy.setSortKey("name")
    assert _names(proxy) == ["Alien", "blade", "Coma"]


def test_sort_by_year_newest_first_missing_last(qapp: object) -> None:
    _, proxy = _build(
        [
            _item("1", "Old", year="1980"),
            _item("2", "NoYear"),
            _item("3", "New", year="2020"),
            _item("4", "Range", year="2010-2015"),
        ]
    )
    proxy.setSortKey("year")
    assert _names(proxy) == ["New", "Range", "Old", "NoYear"]


def test_sort_by_rating_highest_first_missing_last(qapp: object) -> None:
    _, proxy = _build(
        [
            _item("1", "Mid", rating="6.5"),
            _item("2", "None"),
            _item("3", "Top", rating="8.9"),
            _item("4", "Junk", rating="N/A"),
        ]
    )
    proxy.setSortKey("rating")
    assert _names(proxy) == ["Top", "Mid", "None", "Junk"]


def test_rating_parses_loose_addon_formats(qapp: object) -> None:
    _, proxy = _build(
        [
            _item("1", "SlashTen", rating="8.1/10"),
            _item("2", "Comma", rating="9,2"),
            _item("3", "Prefixed", rating="IMDb 7.4"),
            _item("4", "None"),
        ]
    )
    proxy.setSortKey("rating")
    assert _names(proxy) == ["Comma", "SlashTen", "Prefixed", "None"]


def test_all_items_unrated_keeps_source_order(qapp: object) -> None:
    # Catalogs without rating data (e.g. IMDB Catalogs addon) must not be
    # shuffled by a rating sort — everything ties, stable sort preserves order.
    _, proxy = _build([_item("1", "A"), _item("2", "B"), _item("3", "C")])
    proxy.setSortKey("rating")
    assert _names(proxy) == ["A", "B", "C"]


def test_filter_and_sort_combine(qapp: object) -> None:
    _, proxy = _build(
        [
            _item("1", "Alien", year="1979"),
            _item("2", "Aliens", year="1986"),
            _item("3", "Alien 3", year="1992"),
            _item("4", "Blade", year="1998"),
        ]
    )
    proxy.setSortKey("year")
    proxy.setFilterText("alien")
    assert _names(proxy) == ["Alien 3", "Aliens", "Alien"]
    proxy.setFilterText("")
    assert _names(proxy) == ["Blade", "Alien 3", "Aliens", "Alien"]


def test_default_restores_source_order_after_sort(qapp: object) -> None:
    _, proxy = _build([_item("1", "Zebra"), _item("2", "Apple"), _item("3", "Mango")])
    proxy.setSortKey("name")
    assert _names(proxy) == ["Apple", "Mango", "Zebra"]
    proxy.setSortKey("default")
    assert _names(proxy) == ["Zebra", "Apple", "Mango"]


def test_appended_rows_respect_active_sort(qapp: object) -> None:
    model, proxy = _build([_item("1", "B", year="2000"), _item("2", "C", year="1990")])
    proxy.setSortKey("year")
    model.append_items([_item("3", "A", year="2010")])
    assert _names(proxy) == ["A", "B", "C"]


def test_append_items_dedups_by_id(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([_item("1", "A"), _item("2", "B")])
    appended = model.append_items([_item("2", "B"), _item("3", "C")])
    assert appended == 1
    assert model.rowCount() == 3
    assert model.item_at(2).id == "3"
    assert model.append_items([_item("1", "A")]) == 0
    assert model.rowCount() == 3


def test_year_and_rating_roles_exposed(qapp: object) -> None:
    model = PosterGridModel()
    model.set_items([_item("1", "Film", year="1999", rating="7.5")])
    index = model.index(0, 0)
    assert model.data(index, PosterGridModel.YearRole) == "1999"
    assert model.data(index, PosterGridModel.RatingRole) == "7.5"
    names = {bytes(v).decode() for v in model.roleNames().values()}
    assert {"year", "rating"} <= names
