"""Use case: fetch full meta for one item."""

from __future__ import annotations

import asyncio

from gravitas.application.addon_repository import AddonRepository
from gravitas.domain.models import MediaType, MetaDetail


class GetDetail:
    """Fetch a title's meta, sharing a fetch that is already running.

    A poster the pointer rests on starts its title's meta early (see
    DetailController.prefetchTitle), and the click that usually follows must
    join that request rather than start a second one. Only running fetches
    are shared: a finished answer lives in the addon client's own cache.
    """

    def __init__(self, repo: AddonRepository) -> None:
        self._repo = repo
        self._running: dict[tuple[MediaType, str], asyncio.Task[MetaDetail]] = {}

    async def __call__(self, type: MediaType, id: str) -> MetaDetail:
        key = (type, id)
        task = self._running.get(key)
        if task is None:
            task = asyncio.ensure_future(self._repo.meta(type, id))
            self._running[key] = task
            task.add_done_callback(lambda done: self._forget(key, done))
        # Shielded: a caller that gives up must not cancel a fetch another
        # caller is sharing.
        return await asyncio.shield(task)

    async def stored(self, type: MediaType, id: str) -> MetaDetail | None:
        """The last meta kept on disk for this title, of any age, for the page
        to show while __call__ fetches a fresh one."""
        return await self._repo.stored_meta(type, id)

    def _forget(self, key: tuple[MediaType, str], task: asyncio.Task[MetaDetail]) -> None:
        if self._running.get(key) is task:
            del self._running[key]
        # Retrieve the outcome so a prefetch nobody awaited cannot log
        # "exception was never retrieved"; the awaiting callers get it anyway.
        if not task.cancelled():
            task.exception()
