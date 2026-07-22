"""QObject bridge: a stremio:// link -> a confirmation -> an addon install.

A link is a web page asking a media centre to install a data source that will
then serve its catalogs, metadata and stream URLs. Any page the user visits can
fire one, so nothing here installs without the user seeing what and from whom
and saying yes.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Signal, Slot
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.application.deep_link import parse_deep_link
from gravitas.application.preview_addon import PreviewAddon
from gravitas.domain.errors import GravitasError
from gravitas.domain.models import AddonManifest
from gravitas.presentation.external_url import open_in_browser


class _InstallsAddon(Protocol):
    async def install(self, url: str) -> None: ...


class DeepLinkController(QObject):
    errorOccurred = Signal(str)
    # Payload for the confirmation dialog. The Qt type name is the documented
    # way to carry a dict to QML; PySide's stub only admits Python types.
    installRequested = Signal("QVariantMap")  # type: ignore[arg-type]
    # A link clicked in a browser should bring Gravitas forward.
    activateRequested = Signal()

    def __init__(self, preview: PreviewAddon, addons: _InstallsAddon) -> None:
        super().__init__()
        self._preview = preview
        self._addons = addons
        self._pending: tuple[str, AddonManifest] | None = None

    @asyncSlot(str)  # type: ignore[untyped-decorator]
    async def handleLink(self, link: str) -> None:
        # Raise first, whatever the link turns out to be: the user clicked
        # something and expects the app, even if the answer is an error toast.
        self.activateRequested.emit()
        try:
            url = parse_deep_link(link)
        except GravitasError as exc:
            self.errorOccurred.emit(str(exc))
            return
        try:
            manifest = await self._preview(url)
        except GravitasError as exc:
            self.errorOccurred.emit(f"Could not read that addon: {exc}")
            return
        self._pending = (url, manifest)
        self.installRequested.emit(self._payload(url, manifest))

    def _payload(self, url: str, manifest: AddonManifest) -> dict[str, Any]:
        hints = manifest.behavior_hints
        return {
            "name": manifest.name,
            "version": manifest.version,
            # The host, not the addon's self-declared name, is who is being
            # trusted -- and the only part of this a page cannot make up.
            "host": urlsplit(url).netloc,
            "description": manifest.description or "",
            "logo": manifest.logo or "",
            "adult": hints.adult,
            "p2p": hints.p2p,
            # Such an addon serves nothing until configured on its own page, so
            # the dialog offers that instead of a dead install.
            "configurationRequired": hints.configuration_required,
            "configureUrl": manifest.configure_url,
        }

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def confirmInstall(self) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return
        url, manifest = pending
        if manifest.behavior_hints.configuration_required:
            # Defence in depth: the dialog does not offer Install for these, so
            # reaching here means the guard above it failed.
            self.errorOccurred.emit(f"{manifest.name} must be configured before installing")
            return
        await self._addons.install(url)

    @Slot()
    def cancelInstall(self) -> None:
        self._pending = None

    @Slot(str)
    def openConfigure(self, url: str) -> None:
        """Send the user to the addon's own configuration page."""
        self._pending = None
        open_in_browser(url)
