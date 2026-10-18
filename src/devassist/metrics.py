"""Run metrics: what a run used in rounds, tokens, money and time.

Each round is priced at the rates of the model that answered it, because a
run can mix models and the fallback costs many times more per token.
"""

from collections.abc import Sequence

from devassist.config import PRICES, Price
from devassist.models import Attempt, RunMetrics

TOKENS_PER_MILLION = 1_000_000


def price_for(model: str) -> Price | None:
    """Look up a model's list price.

    Args:
        model: A model ID. A dated ID such as ``claude-haiku-5-5-20261001``
            matches the entry it starts with, followed by a hyphen.

    Returns:
        The price, or ``None`` if the model isn't in the price table.
    """
    if model in PRICES:
        return PRICES[model]
    matches = [name for name in PRICES if model.startswith(f"{name}-")]
    return PRICES[max(matches, key=len)] if matches else None


def _cost(attempt: Attempt) -> float | None:
    """Price one round at its model's rates, or ``None`` if the model has no price."""
    price = price_for(attempt.model)
    if price is None:
        return None
    dollars = (
        attempt.input_tokens * price.input_per_million
        + attempt.output_tokens * price.output_per_million
    )
    return dollars / TOKENS_PER_MILLION


def summarize(attempts: Sequence[Attempt], latency_seconds: float) -> RunMetrics:
    """Total up a run's rounds.

    Args:
        attempts: Every round of the run, in order.
        latency_seconds: Wall-clock time for the whole run.

    Returns:
        The run's metrics. The cost is ``None`` if any round's model has no
        price, since a partial total would understate it.
    """
    unpriced = [attempt.model for attempt in attempts if price_for(attempt.model) is None]
    costs = [_cost(attempt) for attempt in attempts]
    return RunMetrics(
        rounds=len(attempts),
        models=[attempt.model for attempt in attempts],
        escalations=sum(1 for attempt in attempts if attempt.escalated),
        retries=sum(1 for attempt in attempts if attempt.retried),
        input_tokens=sum(attempt.input_tokens for attempt in attempts),
        output_tokens=sum(attempt.output_tokens for attempt in attempts),
        cost_usd=None if unpriced else sum(cost for cost in costs if cost is not None),
        unpriced_models=unpriced,
        latency_seconds=latency_seconds,
    )
