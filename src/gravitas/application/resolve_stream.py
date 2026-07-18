"""Use case: fetch streams and keep only directly-playable URLs (MVP: no torrents)."""

from __future__ import annotations

import logging

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.errors import NoStreams
from gravitas.domain.models import MediaType, Stream

_log = logging.getLogger(__name__)


class ResolveStream:
    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo

    async def __call__(self, type: MediaType, id: str) -> list[Stream]:
        streams = await self._repo.streams(type, id)
        direct = [s for s in streams if s.is_direct]
        if not direct:
            # Distinguish "addons had nothing" from "addons had only torrents"
            # — the toast says the same thing either way, the log should not.
            _log.warning(
                "no playable streams for %s %s (%d torrent/external-only dropped)",
                type,
                id,
                len(streams),
            )
            raise NoStreams(f"no direct-URL streams for {id}")
        _log.info(
            "resolved %d playable streams for %s %s (%d dropped as not direct)",
            len(direct),
            type,
            id,
            len(streams) - len(direct),
        )
        return direct
