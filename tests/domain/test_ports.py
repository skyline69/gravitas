from gravitas.domain.models import AddonManifest, MediaItem
from gravitas.domain.ports import AddonSource


class FakeAddonSource:
    async def fetch_manifest(self, url: str) -> AddonManifest:  # pragma: no cover - shape only
        raise NotImplementedError

    async def fetch_catalog(self, manifest: AddonManifest, ref: object) -> list[MediaItem]:
        raise NotImplementedError

    async def fetch_meta(self, manifest: AddonManifest, type: str, id: str) -> object:
        raise NotImplementedError

    async def fetch_streams(self, manifest: AddonManifest, type: str, id: str) -> list[object]:
        raise NotImplementedError


def test_fake_satisfies_addon_source_protocol() -> None:
    assert isinstance(FakeAddonSource(), AddonSource)
