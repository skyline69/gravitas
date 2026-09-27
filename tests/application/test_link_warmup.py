import asyncio
from collections.abc import Sequence

from gravitas.application.link_warmup import LinkWarmup


class FakeResolver:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def resolve(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str | None:
        self.asked.append(url)
        return url.replace("addon", "cdn")


async def _settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


async def test_a_warmed_link_is_handed_out_once() -> None:
    resolver = FakeResolver()
    clock = [0.0]
    links = LinkWarmup(resolver, clock=lambda: clock[0])
    links.warm("https://addon/1")
    await _settle()
    assert links.take("https://addon/1") == "https://cdn/1"
    # Once: a retry after a failure must go through the addon, not this again.
    assert links.take("https://addon/1") is None


async def test_an_old_resolution_is_not_trusted() -> None:
    clock = [0.0]
    links = LinkWarmup(FakeResolver(), clock=lambda: clock[0])
    links.warm("https://addon/1")
    await _settle()
    clock[0] = LinkWarmup.TTL_S + 1
    assert links.take("https://addon/1") is None


async def test_warming_the_same_link_twice_asks_once() -> None:
    resolver = FakeResolver()
    links = LinkWarmup(resolver)
    links.warm("https://addon/1")
    links.warm("https://addon/1")  # still running
    await _settle()
    links.warm("https://addon/1")  # already resolved
    await _settle()
    assert resolver.asked == ["https://addon/1"]


async def test_an_unresolvable_link_leaves_nothing_to_take() -> None:
    class Nothing:
        async def resolve(self, url: str, headers: Sequence[tuple[str, str]] = ()) -> str | None:
            return None

    links = LinkWarmup(Nothing())
    links.warm("https://addon/1")
    await _settle()
    assert links.take("https://addon/1") is None
