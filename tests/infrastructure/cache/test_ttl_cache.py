from gravitas.infrastructure.cache.ttl_cache import TtlCache, json_size


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


def test_json_size_counts_nested_containers_and_strings() -> None:
    """The budget is only as good as the measurement: sys.getsizeof on a dict
    reports the table, not the megabytes of strings hanging off it."""
    import sys

    payload = {"a": "x" * 100_000}
    assert sys.getsizeof(payload) < 1_000  # what a naive measurement would say
    assert json_size(payload) > 100_000
    assert json_size({"a": "x"}) < 1_000


def test_json_size_counts_a_shared_object_once() -> None:
    shared = {"payload": "y" * 10_000}
    twice = json_size({"a": shared, "b": shared})
    once = json_size({"a": shared})
    assert twice < once + 10_000


def test_evicts_least_recently_used_once_over_the_byte_budget() -> None:
    cache: TtlCache[dict[str, str]] = TtlCache(max_bytes=json_size({"v": "x" * 4_000}) * 2)
    cache.put("a", {"v": "x" * 4_000}, ttl=60)
    cache.put("b", {"v": "y" * 4_000}, ttl=60)
    cache.get("a")  # 'b' is now the least recently used
    cache.put("c", {"v": "z" * 4_000}, ttl=60)
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_a_single_entry_larger_than_the_budget_is_not_stored() -> None:
    """Storing it would blow the budget on its own and evict everything else
    for a value that cannot fit."""
    cache: TtlCache[dict[str, str]] = TtlCache(max_bytes=1_000)
    cache.put("huge", {"v": "x" * 50_000}, ttl=60)
    assert cache.get("huge") is None
    assert len(cache) == 0


def test_replacing_a_key_replaces_its_cost_too() -> None:
    cache: TtlCache[dict[str, str]] = TtlCache(max_bytes=json_size({"v": "x" * 4_000}) * 2)
    for _ in range(5):
        cache.put("k", {"v": "x" * 4_000}, ttl=60)
    cache.put("other", {"v": "y" * 4_000}, ttl=60)
    # Five writes of the same key must not have consumed five entries' worth.
    assert cache.get("k") is not None
    assert cache.get("other") is not None


def test_expiry_frees_the_bytes_it_was_holding() -> None:
    clock = FakeClock()
    cache: TtlCache[dict[str, str]] = TtlCache(
        clock=clock, max_bytes=json_size({"v": "x" * 4_000}) * 2
    )
    cache.put("a", {"v": "x" * 4_000}, ttl=10)
    clock.now = 11
    assert cache.get("a") is None  # expired, and its cost released with it
    cache.put("b", {"v": "y" * 4_000}, ttl=60)
    cache.put("c", {"v": "z" * 4_000}, ttl=60)
    assert cache.get("b") is not None
    assert cache.get("c") is not None
