from collections.abc import Sequence

import pytest

from evals.stats import bootstrap_interval, mean, percentile

VALUES = [0.0, 1.0, 0.5, 0.2, 0.9, 0.4]


def mean_of_values(indices: Sequence[int]) -> float:
    """The mean of VALUES at these indices, as a bootstrap statistic."""
    return mean([VALUES[i] for i in indices])


def test_mean() -> None:
    assert mean([1.0, 2.0, 6.0]) == 3.0


def test_the_mean_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        mean([])


def test_the_median_interpolates_between_the_two_middle_values() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.5) == 2.5


def test_the_ends_are_the_minimum_and_the_maximum() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.0) == 1.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 1.0) == 4.0


def test_p95_of_one_to_one_hundred_matches_the_usual_definition() -> None:
    assert percentile([float(v) for v in range(1, 101)], 0.95) == pytest.approx(95.05)


def test_the_percentile_of_one_value_is_that_value() -> None:
    assert percentile([7.0], 0.95) == 7.0


def test_percentile_rejects_empty_data() -> None:
    with pytest.raises(ValueError, match="empty"):
        percentile([], 0.5)


def test_percentile_rejects_a_quantile_outside_zero_to_one() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        percentile([1.0], 1.5)


def test_a_constant_statistic_has_an_interval_of_zero_width() -> None:
    assert bootstrap_interval(5, lambda _: 0.25, resamples=200, seed=1) == (0.25, 0.25)


def test_the_same_seed_gives_the_same_interval() -> None:
    first = bootstrap_interval(len(VALUES), mean_of_values, resamples=500, seed=7)
    second = bootstrap_interval(len(VALUES), mean_of_values, resamples=500, seed=7)

    assert first == second


def test_the_interval_brackets_the_mean_and_stays_within_the_data() -> None:
    low, high = bootstrap_interval(len(VALUES), mean_of_values, resamples=2_000, seed=7)

    assert min(VALUES) <= low < mean(VALUES) < high <= max(VALUES)


def test_each_resample_draws_every_case_slot_with_replacement() -> None:
    draws: list[list[int]] = []

    def record_draw(indices: Sequence[int]) -> float:
        draws.append(list(indices))
        return 0.0

    bootstrap_interval(3, record_draw, resamples=50, seed=3)

    assert len(draws) == 50
    assert all(len(draw) == 3 and set(draw) <= {0, 1, 2} for draw in draws)
    assert any(len(set(draw)) < 3 for draw in draws)


def test_the_bootstrap_needs_at_least_one_case() -> None:
    with pytest.raises(ValueError, match="at least one case"):
        bootstrap_interval(0, mean_of_values, resamples=10, seed=1)
