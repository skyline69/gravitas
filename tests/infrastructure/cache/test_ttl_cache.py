from gravitas.infrastructure.cache.ttl_cache import TtlCache


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_miss_returns_none() -> None:
    assert TtlCache().get("nope") is None


def test_hit_returns_the_stored_value() -> None:
    cache: TtlCache[dict[str, int]] = TtlCache()
    cache.put("k", {"a": 1}, ttl=60)
    assert cache.get("k") == {"a": 1}


def test_entry_expires_once_its_ttl_has_passed() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(clock=clock)
    cache.put("k", "v", ttl=60)
    clock.now = 59.0
    assert cache.get("k") == "v"
    clock.now = 60.0
    # At the boundary the entry is stale: a TTL of 60 means 60 seconds of
    # freshness, not 61.
    assert cache.get("k") is None


def test_an_expired_entry_is_dropped_rather_than_left_to_rot() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(clock=clock)
    cache.put("k", "v", ttl=10)
    clock.now = 99.0
    cache.get("k")
    assert len(cache) == 0


def test_zero_ttl_is_not_stored() -> None:
    cache: TtlCache[str] = TtlCache()
    cache.put("k", "v", ttl=0)
    # The default for anything that must never be cached (stream links expire).
    assert cache.get("k") is None
    assert len(cache) == 0


def test_negative_ttl_is_not_stored() -> None:
    cache: TtlCache[str] = TtlCache()
    cache.put("k", "v", ttl=-1)
    assert cache.get("k") is None


def test_infinite_ttl_never_expires() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(clock=clock)
    cache.put("k", "v", ttl=float("inf"))
    clock.now = 10**9
    assert cache.get("k") == "v"


def test_put_replaces_an_existing_key() -> None:
    cache: TtlCache[str] = TtlCache()
    cache.put("k", "old", ttl=60)
    cache.put("k", "new", ttl=60)
    assert cache.get("k") == "new"
    assert len(cache) == 1


def test_evicts_least_recently_used_at_capacity() -> None:
    cache: TtlCache[str] = TtlCache(max_entries=2)
    cache.put("a", "1", ttl=60)
    cache.put("b", "2", ttl=60)
    cache.get("a")  # a is now the most recently used
    cache.put("c", "3", ttl=60)
    # Bounded on purpose: an unbounded response cache is a memory leak with a
    # friendly name.
    assert len(cache) == 2
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"


def test_clear() -> None:
    cache: TtlCache[str] = TtlCache()
    cache.put("k", "v", ttl=60)
    cache.clear()
    assert cache.get("k") is None


def test_uses_a_monotonic_clock_by_default() -> None:
    # Wall-clock jumps (NTP, DST, a user fixing their clock) must not make a
    # fresh entry look years old, nor a stale one look fresh.
    import time

    cache: TtlCache[str] = TtlCache()
    assert cache._clock is time.monotonic
