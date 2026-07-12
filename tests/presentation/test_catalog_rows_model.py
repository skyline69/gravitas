from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.models import MediaItem
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel
from gravitas.presentation.models.poster_grid_model import PosterGridModel


def _row(title: str, catalog_id: str, name: str) -> CatalogRow:
    return CatalogRow(
        title=title,
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
    assert {"title", "type", "catalogId", "posters"} <= names
