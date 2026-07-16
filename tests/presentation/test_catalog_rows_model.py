from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel


def _row(title: str, catalog_id: str, name: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type="movie",
        catalog_id=catalog_id,
        items=[MediaItem(id="tt1", type="movie", name=name, poster="http://p/1.jpg")],
    )


def test_set_rows_exposes_roles(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows([_row("Top", "top", "A")])
    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, CatalogRowsModel.TitleRole) == "Top"
    assert model.data(index, CatalogRowsModel.AddonIdRole) == "a"
    assert model.data(index, CatalogRowsModel.TypeRole) == "movie"
    assert model.data(index, CatalogRowsModel.CatalogIdRole) == "top"


def test_posters_role_returns_populated_poster_model(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows([_row("Top", "top", "A")])
    posters = model.data(model.index(0, 0), CatalogRowsModel.PostersRole)
    assert isinstance(posters, PosterGridModel)
    assert posters.rowCount() == 1
    poster_index = posters.index(0, 0)
    assert posters.data(poster_index, PosterGridModel.NameRole) == "A"


def test_set_rows_resets(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows([_row("Top", "top", "A"), _row("New", "new", "B")])
    assert model.rowCount() == 2
    model.set_rows([_row("Only", "only", "C")])
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), CatalogRowsModel.TitleRole) == "Only"


def test_role_names_are_stringified(qapp: object) -> None:
    model = CatalogRowsModel()
    names = {bytes(v).decode() for v in model.roleNames().values()}
    assert {"title", "addonId", "type", "catalogId", "posters"} <= names


def _typed_row(title: str, type_: str, catalog_id: str) -> CatalogRow:
    return CatalogRow(
        title=title,
        addon_id="a",
        type=type_,  # type: ignore[arg-type]
        catalog_id=catalog_id,
        items=[MediaItem(id="tt1", type=type_, name="X", poster=None)],  # type: ignore[arg-type]
    )


_ROWS = [
    _typed_row("Popular Movies", "movie", "top"),
    _typed_row("New Series", "series", "year"),
    _typed_row("Trending Now", "movie", "trending"),
    _typed_row("Documentaries", "series", "docs"),
]


def _titles(model: CatalogRowsModel) -> list[str]:
    return [
        model.data(model.index(i, 0), CatalogRowsModel.TitleRole) for i in range(model.rowCount())
    ]


def test_filter_all_shows_every_row(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    assert model.rowCount() == 4


def test_filter_movie(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("movie")
    assert _titles(model) == ["Popular Movies", "Trending Now"]


def test_filter_series(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("series")
    assert _titles(model) == ["New Series", "Documentaries"]


def test_filter_trending_matches_title_or_catalog_id_case_insensitive(
    qapp: object,
) -> None:
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("trending")
    # "Popular Movies" (title kw), "Trending Now" (title + catalog_id kw)
    assert _titles(model) == ["Popular Movies", "Trending Now"]


def test_filter_persists_across_set_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    model.set_filter("series")
    model.set_rows(_ROWS)
    assert _titles(model) == ["New Series", "Documentaries"]


def test_refresh_progress_reaches_rows_hidden_by_the_active_filter(qapp: object) -> None:
    """refresh_progress must fan out over _all_rows, not the filtered _rows —
    otherwise switching filters back to a row that was hidden during a
    playback session would show a stale bar until something else forced a
    reset. Movies is the active filter here, so the Series row's poster model
    must still receive dataChanged even though it is currently filtered out."""
    model = CatalogRowsModel()
    model.set_rows(_ROWS)
    model.set_filter("movie")
    assert _titles(model) == ["Popular Movies", "Trending Now"]

    poster_models = [row[4] for row in model._all_rows]
    assert len(poster_models) == 4
    seen: list[list[tuple[int, list[int]]]] = [[] for _ in poster_models]
    for i, pm in enumerate(poster_models):
        pm.dataChanged.connect(lambda _tl, _br, roles, i=i: seen[i].append((0, list(roles))))

    model.refresh_progress()

    assert all(fired for fired in seen), "every row's poster model must fire dataChanged"
    # "New Series" (index 1) is filtered out under "movie" but must still refresh.
    assert seen[1], "a filtered-out row's poster model was not refreshed"
