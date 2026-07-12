from gravitas.application.addon_repository import AddonRepository
from gravitas.application.install_addon import InstallAddon
from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import (
    AddonManifest,
    CatalogRef,
    MediaItem,
    MediaType,
    MetaDetail,
    Stream,
)
from gravitas.presentation.controllers.addon_controller import AddonController


class FakeSource:
    async def fetch_manifest(self, url: str) -> AddonManifest:
        return AddonManifest(
            id="fake",
            name="Fake Addon",
            version="1",
            resources=("catalog", "meta", "stream"),
            types=("movie",),
            catalogs=(CatalogRef(type="movie", id="top", name="Top"),),
            base_url=url,
        )

    async def fetch_catalog(self, manifest: AddonManifest, ref: CatalogRef) -> list[MediaItem]:
        return [MediaItem(id="tt1", type="movie", name="A", poster=None)]

    async def fetch_meta(self, manifest: AddonManifest, type: MediaType, id: str) -> MetaDetail:
        raise NotImplementedError

    async def fetch_streams(
        self, manifest: AddonManifest, type: MediaType, id: str
    ) -> list[Stream]:
        raise NotImplementedError


class UnreachableSource(FakeSource):
    async def fetch_manifest(self, url: str) -> AddonManifest:
        raise AddonUnreachable("boom")


class FakeDetailController:
    def __init__(self) -> None:
        self.bound: list[AddonManifest] = []

    def bind_manifest(self, manifest: AddonManifest) -> None:
        self.bound.append(manifest)


class FakeCatalogController:
    def __init__(self) -> None:
        self.refresh_calls = 0

    async def load_catalog(self) -> None:
        self.refresh_calls += 1


async def test_add_addon_success(qapp: object) -> None:
    repo = AddonRepository(FakeSource())
    detail = FakeDetailController()
    catalog = FakeCatalogController()
    controller = AddonController(InstallAddon(repo), detail, catalog)  # type: ignore[arg-type]

    installed: list[str] = []
    controller.addonInstalled.connect(installed.append)

    await controller.addAddon("https://a/manifest.json")

    assert repo.installed()[0].id == "fake"
    assert detail.bound[0].id == "fake"
    assert catalog.refresh_calls == 1
    assert installed == ["Fake Addon"]


async def test_add_addon_error_emits_signal(qapp: object) -> None:
    repo = AddonRepository(UnreachableSource())
    detail = FakeDetailController()
    catalog = FakeCatalogController()
    controller = AddonController(InstallAddon(repo), detail, catalog)  # type: ignore[arg-type]

    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)

    await controller.addAddon("https://a/manifest.json")

    assert errors == ["boom"]
    assert detail.bound == []
    assert catalog.refresh_calls == 0
    assert repo.installed() == []
