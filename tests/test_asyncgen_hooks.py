"""An async generator left unfinished is closed on the qasync loop.

qasync's run_forever does not install asyncio's async-generator hooks, so
without main._adopt_asyncgen_hooks an abandoned generator -- httpcore's
response stream, most often -- is closed synchronously by the garbage
collector, outside any loop, and its cleanup's `await` surfaces as "async
generator ignored GeneratorExit" or "RuntimeError: no running event loop".
"""

from __future__ import annotations

import asyncio
import gc
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
import qasync

from gravitas.main import _adopt_asyncgen_hooks


def test_an_abandoned_async_generator_is_closed_on_the_loop(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    unraisable: list[Any] = []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
    closed: list[bool] = []

    async def body() -> AsyncIterator[bytes]:
        try:
            yield b"first"
            yield b"second"
        finally:
            # httpcore's stream awaits in its cleanup, which is what a
            # synchronous close cannot do.
            await asyncio.sleep(0)
            closed.append(True)

    async def abandon() -> None:
        stream = body()
        await anext(stream)
        del stream
        gc.collect()
        await asyncio.sleep(0.05)

    previous = sys.get_asyncgen_hooks()
    loop = qasync.QEventLoop(qapp)
    try:
        _adopt_asyncgen_hooks(loop)
        loop.run_until_complete(abandon())
    finally:
        sys.set_asyncgen_hooks(*previous)
        loop.close()
    assert unraisable == []
    assert closed == [True]
