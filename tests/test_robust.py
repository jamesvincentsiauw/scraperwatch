import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scraperwatch.stats.robust import median, robust_spread, wilson_interval


def test_median_odd_and_even() -> None:
    assert median([3, 1, 2]) == 2
    assert median([4, 1, 3, 2]) == 2.5


def test_median_rejects_empty() -> None:
    with pytest.raises(ValueError):
        median([])


def test_spread_ignores_one_outlier() -> None:
    steady = [0.95, 0.96, 0.95, 0.97, 0.96]
    assert robust_spread([*steady, 0.10]) < 0.05


def test_spread_of_constant_values_is_zero() -> None:
    assert robust_spread([1.0, 1.0, 1.0]) == 0


def test_wilson_matches_known_value() -> None:
    low, high = wilson_interval(8, 10)
    assert math.isclose(low, 0.4902, abs_tol=1e-4)
    assert math.isclose(high, 0.9433, abs_tol=1e-4)


def test_wilson_narrows_with_more_data() -> None:
    small = wilson_interval(6, 10)
    large = wilson_interval(6000, 10000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_without_data_is_uninformative() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


@given(
    st.integers(min_value=1, max_value=100_000).flatmap(
        lambda n: st.tuples(st.integers(0, n), st.just(n))
    )
)
def test_wilson_contains_the_observed_share(case: tuple[int, int]) -> None:
    successes, total = case
    low, high = wilson_interval(successes, total)
    assert 0 <= low <= successes / total <= high <= 1


@given(st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=50))
def test_median_is_within_range(values: list[float]) -> None:
    assert min(values) <= median(values) <= max(values)
    assert robust_spread(values) >= 0
