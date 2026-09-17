import random

from hypothesis import given
from hypothesis import strategies as st

from scraperwatch.stats.distinct import DistinctCounter, HyperLogLog, key_hash


def test_key_hash_is_stable_and_order_sensitive() -> None:
    assert key_hash(["shopee", 123]) == key_hash(["shopee", 123])
    assert key_hash(["shopee", 123]) != key_hash([123, "shopee"])
    assert key_hash(["shopee", 123]) != key_hash(["shopee", "123"])
    assert 0 <= key_hash(["x"]) < 2**64


def test_exact_counter_reports_repeats() -> None:
    counter = DistinctCounter()
    assert counter.add(1) is False
    assert counter.add(2) is False
    assert counter.add(1) is True
    assert counter.distinct() == 2
    assert counter.total == 3


def test_counter_switches_to_estimate_past_the_limit() -> None:
    counter = DistinctCounter(exact_limit=1_000)
    rng = random.Random(7)
    keys = [rng.getrandbits(64) for _ in range(20_000)]
    for k in keys:
        counter.add(k)
    for k in keys[:5_000]:
        assert counter.add(k) is None

    assert not counter.exact
    assert counter.total == 25_000
    assert abs(counter.distinct() - 20_000) / 20_000 < 0.03


def test_hyperloglog_accuracy_on_a_million_keys() -> None:
    hll = HyperLogLog()
    rng = random.Random(42)
    for _ in range(1_000_000):
        hll.add(rng.getrandbits(64))
    assert abs(hll.estimate() - 1_000_000) / 1_000_000 < 0.03


def test_hyperloglog_small_counts_use_linear_counting() -> None:
    hll = HyperLogLog()
    for i in range(100):
        hll.add(key_hash([i]))
    assert abs(hll.estimate() - 100) <= 3


@given(st.lists(st.integers(min_value=0, max_value=2**64 - 1), max_size=300))
def test_exact_mode_matches_set(keys: list[int]) -> None:
    counter = DistinctCounter()
    for k in keys:
        counter.add(k)
    assert counter.distinct() == len(set(keys))
