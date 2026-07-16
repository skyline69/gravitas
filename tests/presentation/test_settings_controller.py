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


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list[object] = []

    def load(self) -> object:
        raise NotImplementedError

    def save(self, settings: object) -> None:
        self.saved.append(settings)


async def test_set_tmdb_key_persists(qapp: object) -> None:
    from gravitas.domain.models import PersistedSettings

    repo = AddonRepository(FakeSource())
    holder = _KeyHolder()
    store = _FakeStore()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), holder, store
    )
    c.setTmdbKey("ABC")
    assert store.saved == [PersistedSettings(addon_urls=(), tmdb_key="ABC")]
    assert c.tmdbKey == "ABC"


async def test_remove_addon_persists_remaining_urls(qapp: object) -> None:
    from gravitas.domain.models import PersistedSettings

    repo = AddonRepository(FakeSource())
    await repo.install("https://a/")
    await repo.install("https://b/")
    store = _FakeStore()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), None, store
    )
    await c.removeAddon("https://a/")
    assert store.saved == [PersistedSettings(addon_urls=("https://b/",), tmdb_key=None)]


class _StyleHolder:
    def __init__(self) -> None:
        from gravitas.domain.models import SubtitleStyle

        self.style = SubtitleStyle()


async def test_set_mdblist_key_updates_and_persists(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    tmdb_holder = _KeyHolder()
    mdb_holder = _KeyHolder()
    store = _FakeStore()
    styles = _StyleHolder()
    c = SettingsController(
        UninstallAddon(repo),
        repo,
        AddonListModel(),
        FakeCatalogController(),
        tmdb_holder,
        store,
        styles,
        mdb_holder,
    )

    assert c.mdblistKey == ""
    c.setMdblistKey("  key-xyz  ")
    assert mdb_holder.key == "key-xyz"
    assert c.mdblistKey == "key-xyz"
    assert store.saved[-1].mdblist_key == "key-xyz"  # type: ignore[attr-defined]

    c.setMdblistKey("")
    assert mdb_holder.key is None


async def test_subtitle_style_updates_persist_and_notify(qapp: object) -> None:
    from gravitas.domain.models import SubtitleStyle

    repo = AddonRepository(FakeSource())
    store = _FakeStore()
    styles = _StyleHolder()
    c = SettingsController(
        UninstallAddon(repo), repo, AddonListModel(), FakeCatalogController(), None, store, styles
    )
    fired: list[None] = []
    c.subtitleStyleChanged.connect(lambda: fired.append(None))

    c.setSubFontSize(70)
    c.setSubColor("#FFE400")
    c.setSubBold(True)
    assert styles.style.font_size == 70
    assert styles.style.color == "#FFE400"
    assert styles.style.bold is True
    assert len(fired) == 3
    assert len(store.saved) == 3

    c.setSubFontSize(999)  # clamped
    assert styles.style.font_size == 100

    c.resetSubtitleStyle()
    assert styles.style == SubtitleStyle()
