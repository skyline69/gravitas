"""The install flow a web page can trigger. What matters most here is what does
NOT happen: no link installs anything without a confirmation."""

from __future__ import annotations

from typing import Any

from gravitas.domain.errors import AddonUnreachable
from gravitas.domain.models import AddonBehaviorHints, AddonManifest
from gravitas.presentation.controllers.deep_link_controller import DeepLinkController

_LINK = "stremio://addon.example/manifest.json"
_URL = "https://addon.example/manifest.json"


def _manifest(**kw: object) -> AddonManifest:
    base: dict[str, object] = {
        "id": "org.addon",
        "name": "Addon",
        "version": "1.2.3",
        "resources": (),
        "types": (),
        "catalogs": (),
        "base_url": "https://addon.example/",
    }
    base.update(kw)
    return AddonManifest(**base)  # type: ignore[arg-type]


class FakePreview:
    def __init__(self, manifest: AddonManifest | None = None, error: Exception | None = None):
        self.manifest = manifest if manifest is not None else _manifest()
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, url: str) -> AddonManifest:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.manifest


class FakeAddons:
    def __init__(self) -> None:
        self.installed: list[str] = []

    async def install(self, url: str) -> None:
        self.installed.append(url)


def _controller(
    preview: FakePreview | None = None, addons: FakeAddons | None = None
) -> tuple[DeepLinkController, FakePreview, FakeAddons, list[Any], list[str]]:
    preview = preview or FakePreview()
    addons = addons or FakeAddons()
    controller = DeepLinkController(preview, addons)  # type: ignore[arg-type]
    requests: list[Any] = []
    errors: list[str] = []
    controller.installRequested.connect(requests.append)
    controller.errorOccurred.connect(errors.append)
    return controller, preview, addons, requests, errors


async def test_link_previews_the_manifest_and_asks_before_installing() -> None:
    controller, preview, addons, requests, errors = _controller()

    await controller.handleLink(_LINK)

    assert preview.calls == [_URL]
    # The decisive assertion: previewing must not install.
    assert addons.installed == []
    assert errors == []
    assert requests[0]["name"] == "Addon"
    assert requests[0]["version"] == "1.2.3"
    # The host is what identifies who is being trusted; a page can claim any name.
    assert requests[0]["host"] == "addon.example"


async def test_confirming_installs_the_previewed_url() -> None:
    controller, _, addons, _, _ = _controller()
    await controller.handleLink(_LINK)

    await controller.confirmInstall()

    assert addons.installed == [_URL]


async def test_cancelling_installs_nothing_and_forgets_the_link() -> None:
    controller, _, addons, _, _ = _controller()
    await controller.handleLink(_LINK)

    controller.cancelInstall()
    await controller.confirmInstall()  # a later confirm must not resurrect it

    assert addons.installed == []


async def test_confirm_without_a_pending_link_installs_nothing() -> None:
    controller, _, addons, _, _ = _controller()

    await controller.confirmInstall()

    assert addons.installed == []


async def test_confirm_twice_installs_once() -> None:
    controller, _, addons, _, _ = _controller()
    await controller.handleLink(_LINK)

    await controller.confirmInstall()
    await controller.confirmInstall()

    assert addons.installed == [_URL]


async def test_unsupported_link_errors_and_never_reaches_the_network() -> None:
    controller, preview, addons, requests, errors = _controller()

    await controller.handleLink("stremio:///detail/movie/tt1")

    assert preview.calls == []  # nothing fetched
    assert addons.installed == []
    assert requests == []
    assert errors and "detail" in errors[0]


async def test_non_stremio_link_is_refused() -> None:
    controller, preview, addons, requests, errors = _controller()

    await controller.handleLink("javascript:alert(1)")

    assert preview.calls == []
    assert addons.installed == []
    assert requests == []
    assert errors


async def test_unreachable_addon_errors_without_a_dialog() -> None:
    controller, _, addons, requests, errors = _controller(
        preview=FakePreview(error=AddonUnreachable("down"))
    )

    await controller.handleLink(_LINK)

    assert requests == []
    assert addons.installed == []
    assert errors and "Could not read that addon" in errors[0]


async def test_configuration_required_addon_offers_configuring_not_installing() -> None:
    manifest = _manifest(behavior_hints=AddonBehaviorHints(configuration_required=True))
    controller, _, addons, requests, _ = _controller(preview=FakePreview(manifest))

    await controller.handleLink(_LINK)

    assert requests[0]["configurationRequired"] is True
    assert requests[0]["configureUrl"] == "https://addon.example/configure"
    assert addons.installed == []


async def test_configuration_required_addon_refuses_a_forced_confirm() -> None:
    # The dialog does not offer Install for these; this pins the guard behind it.
    manifest = _manifest(
        name="Needs Setup", behavior_hints=AddonBehaviorHints(configuration_required=True)
    )
    controller, _, addons, _, errors = _controller(preview=FakePreview(manifest))
    await controller.handleLink(_LINK)

    await controller.confirmInstall()

    assert addons.installed == []
    assert errors and "must be configured" in errors[0]


async def test_adult_and_p2p_hints_reach_the_dialog() -> None:
    manifest = _manifest(behavior_hints=AddonBehaviorHints(adult=True, p2p=True))
    controller, _, _, requests, _ = _controller(preview=FakePreview(manifest))

    await controller.handleLink(_LINK)

    # Surfaced where the decision is actually made.
    assert requests[0]["adult"] is True
    assert requests[0]["p2p"] is True


async def test_a_link_asks_the_window_to_come_forward() -> None:
    controller, _, _, _, _ = _controller()
    activations: list[int] = []
    controller.activateRequested.connect(lambda: activations.append(1))

    await controller.handleLink(_LINK)

    assert activations == [1]


async def test_a_refused_link_still_raises_the_window() -> None:
    # Otherwise the error toast lands on a window the user cannot see.
    controller, _, _, _, _ = _controller()
    activations: list[int] = []
    controller.activateRequested.connect(lambda: activations.append(1))

    await controller.handleLink("nonsense")

    assert activations == [1]
