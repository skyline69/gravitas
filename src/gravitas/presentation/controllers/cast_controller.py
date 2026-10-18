"""QObject bridge: finding TVs, and handing playback to one.

The player owns what plays where (PlayerController.cast_to / stopCasting);
this owns the list of TVs the cast menu shows and the relay each one gets.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Property, QObject, Signal
from qasync import asyncSlot  # type: ignore[import-untyped]

from gravitas.domain.ports import CastDiscovery, CastTarget, StreamRelay
from gravitas.presentation.controllers.player_controller import PlayerController

_log = logging.getLogger(__name__)


class CastController(QObject):
    devicesChanged = Signal()
    scanningChanged = Signal()

    # Long enough for a TV on Wi-Fi to answer the search (SSDP gives them up
    # to the MX value to reply), short enough that the menu fills while open.
    SCAN_S = 2.0

    def __init__(
        self,
        discovery: CastDiscovery,
        relay_factory: Callable[[str], StreamRelay],
        player: PlayerController,
    ) -> None:
        super().__init__()
        self._discovery = discovery
        # Builds a relay listening on the given local address: the one the
        # chosen TV reaches this machine at.
        self._relay_factory = relay_factory
        self._player = player
        self._targets: list[CastTarget] = []
        self._scanning = False

    @Property("QVariantList", notify=devicesChanged)  # type: ignore[arg-type]
    def devices(self) -> list[dict[str, object]]:
        """[{id, title}] in the shape TrackMenu lists."""
        return [{"id": i, "title": t.name} for i, t in enumerate(self._targets)]

    @Property(bool, notify=scanningChanged)
    def scanning(self) -> bool:
        return self._scanning

    @asyncSlot()  # type: ignore[untyped-decorator]
    async def scan(self) -> None:
        """Search the network again. The last list stays up meanwhile: the TV
        found a minute ago is almost always still there."""
        if self._scanning:
            return
        self._scanning = True
        self.scanningChanged.emit()
        try:
            self._targets = await self._discovery.discover(self.SCAN_S)
        except Exception:  # pragma: no cover - discovery must not raise
            _log.exception("searching for TVs failed")
        finally:
            self._scanning = False
            self.scanningChanged.emit()
        self.devicesChanged.emit()

    @asyncSlot(int)  # type: ignore[untyped-decorator]
    async def castTo(self, index: int) -> None:
        if not 0 <= index < len(self._targets):
            return
        target = self._targets[index]
        await self._player.cast_to(target, self._relay_factory(target.local_address))
