from gravitas.application.addon_repository import AddonRepository
from gravitas.application.uninstall_addon import UninstallAddon
from gravitas.presentation.controllers.settings_controller import SettingsController
from gravitas.presentation.models.addon_list_model import AddonListModel
from tests.application.test_addon_repository import FakeSource


class FakeCatalogController:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def load_catalog(self) -> None:
        self.refresh_calls += 1


def _build(
    repo: AddonRepository,
) -> tuple[SettingsController, AddonListModel, FakeCatalogController]:
    model = AddonListModel()
    catalog = FakeCatalogController()
    controller = SettingsController(UninstallAddon(repo), repo, model, catalog)  # type: ignore[arg-type]
    return controller, model, catalog


async def test_refresh_populates_model_with_protected_flag(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    protected = await repo.install("https://a/", protected=True)
    controller, model, _ = _build(repo)
    controller.refreshAddons()
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0), AddonListModel.RemovableRole) is False
    assert model.data(model.index(0, 0), AddonListModel.IdRole) == protected.id


async def test_remove_addon_uninstalls_and_refreshes(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/")  # not protected
    controller, model, catalog = _build(repo)
    controller.refreshAddons()
    await controller.removeAddon(manifest.id)
    assert repo.installed() == []
    assert catalog.refresh_calls == 1
    assert model.rowCount() == 0


async def test_remove_protected_emits_error(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    manifest = await repo.install("https://a/", protected=True)
    controller, _model, catalog = _build(repo)
    controller.refreshAddons()
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    await controller.removeAddon(manifest.id)
    assert len(errors) == 1
    assert repo.installed() != []
    assert catalog.refresh_calls == 0


class _KeyHolder:
    key: str | None = None


async def test_set_tmdb_key_updates_holder(qapp: object) -> None:
    from gravitas.application.addon_repository import AddonRepository
    from gravitas.application.uninstall_addon import UninstallAddon
    from gravitas.presentation.controllers.settings_controller import SettingsController
    from gravitas.presentation.models.addon_list_model import AddonListModel
    from tests.application.test_addon_repository import FakeSource

    repo = AddonRepository(FakeSource())
    holder = _KeyHolder()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), holder
    )
    c.setTmdbKey("ABC")
    assert holder.key == "ABC"
