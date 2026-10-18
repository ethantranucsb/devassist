"""The few statistics the eval needs, from the standard library only.

With 30 cases, a plain-Python bootstrap takes about a second, so numpy isn't
worth a dependency.
"""

import math
import random
from collections.abc import Callable, Sequence


def mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean.

    Raises:
        ValueError: If there are no values.
    """
    if not values:
        raise ValueError("can't average an empty list")
    return math.fsum(values) / len(values)


def percentile(values: Sequence[float], q: float) -> float:
    """Return the q-quantile, interpolating linearly between the two nearest values.

    This is the usual definition (numpy's default), so ``q=0.5`` gives the
    median and ``q=0.95`` gives p95.

    Args:
        values: The data.
        q: The quantile, from 0 to 1.

    Returns:
        The quantile.

    Raises:
        ValueError: If there are no values, or q is outside 0 to 1.
    """
    if not values:
        raise ValueError("can't take a percentile of an empty list")
    if not 0 <= q <= 1:
        raise ValueError(f"the quantile must be between 0 and 1, not {q}")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def bootstrap_interval(
    size: int,
    statistic: Callable[[Sequence[int]], float],
    *,
    resamples: int,
    seed: int,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Estimate a confidence interval by resampling cases with replacement.

    Each resample draws ``size`` case indices with replacement and passes
    them to ``statistic``. When the statistic looks up both configurations'
    results for the same indices, the bootstrap is paired: every resample
    compares the two on the same cases.

    Args:
        size: How many cases there are.
        statistic: Computes the statistic from a list of case indices.
        resamples: How many resamples to draw.
        seed: Seeds the draws, so the interval is the same on every run.
        confidence: The share of resampled statistics the interval covers.

    Returns:
        The percentile interval: the middle ``confidence`` share of the
        resampled statistics.

    Raises:
        ValueError: If there are no cases.
    """
    if size < 1:
        raise ValueError("the bootstrap needs at least one case")
    draw = random.Random(seed)
    cases = range(size)
    estimates = [statistic(draw.choices(cases, k=size)) for _ in range(resamples)]
    tail = (1 - confidence) / 2
    return percentile(estimates, tail), percentile(estimates, 1 - tail)
