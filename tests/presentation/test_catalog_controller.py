from gravitas.application.browse_catalog import CatalogRow
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import MediaItem
from gravitas.presentation.controllers.catalog_controller import CatalogController
from gravitas.presentation.models.catalog_rows_model import CatalogRowsModel


class FakeBrowse:
    async def __call__(self) -> list[CatalogRow]:
        return [
            CatalogRow(
                title="Top",
                addon_id="test",
                type="movie",
                catalog_id="top",
                items=[MediaItem(id="tt1", type="movie", name="A", poster=None)],
            )
        ]


class FailingBrowse:
    async def __call__(self) -> list[CatalogRow]:
        raise AddonUnreachable("boom")


async def test_load_catalog_populates_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    controller = CatalogController(FakeBrowse(), model)  # type: ignore[arg-type]
    loading: list[bool] = []
    controller.loadingChanged.connect(loading.append)

    await controller.load_catalog()

    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, CatalogRowsModel.TitleRole) == "Top"
    assert model.data(index, CatalogRowsModel.CatalogIdRole) == "top"
    assert loading == [True, False]


async def test_load_catalog_emits_error_and_still_clears_loading(qapp: object) -> None:
    model = CatalogRowsModel()
    controller = CatalogController(FailingBrowse(), model)  # type: ignore[arg-type]
    errors: list[str] = []
    loading: list[bool] = []
    controller.errorOccurred.connect(errors.append)
    controller.loadingChanged.connect(loading.append)

    await controller.load_catalog()

    assert errors == ["boom"]
    assert loading == [True, False]
    assert model.rowCount() == 0


async def test_set_filter_narrows_rows(qapp: object) -> None:
    model = CatalogRowsModel()
    controller = CatalogController(FakeBrowse(), model)  # type: ignore[arg-type]
    await controller.load_catalog()
    controller.setFilter("series")
    assert model.rowCount() == 0  # FakeBrowse yields a single movie row
    controller.setFilter("movie")
    assert model.rowCount() == 1
