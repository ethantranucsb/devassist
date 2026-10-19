"""Run metrics: what a run used in rounds, reviews, tokens, money and time.

Each model call is priced at the rates of the model that answered it,
because a run can mix models and the fallback and review model cost many
times more per token.
"""

from collections.abc import Sequence

from devassist.config import PRICES, Price
from devassist.models import Attempt, ReviewCall, RunMetrics

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


def _cost(call: Attempt | ReviewCall) -> float | None:
    """Price one model call at its model's rates, or ``None`` if the model has no price."""
    price = price_for(call.model)
    if price is None:
        return None
    dollars = (
        call.input_tokens * price.input_per_million + call.output_tokens * price.output_per_million
    )
    return dollars / TOKENS_PER_MILLION


def summarize(
    attempts: Sequence[Attempt], latency_seconds: float, review: ReviewCall | None = None
) -> RunMetrics:
    """Total up a run's rounds and its second review.

    Args:
        attempts: Every round of the run, in order.
        latency_seconds: Wall-clock time for the whole run.
        review: The second review, if one ran.

    Returns:
        The run's metrics. The cost is ``None`` if any call's model has no
        price, since a partial total would understate it.
    """
    calls: list[Attempt | ReviewCall] = [*attempts, *([review] if review is not None else [])]
    unpriced = [call.model for call in calls if price_for(call.model) is None]
    costs = [_cost(call) for call in calls]
    return RunMetrics(
        rounds=len(attempts),
        models=[attempt.model for attempt in attempts],
        escalations=sum(1 for attempt in attempts if attempt.escalated),
        retries=sum(1 for call in calls if call.retried),
        reviews=0 if review is None else 1,
        input_tokens=sum(call.input_tokens for call in calls),
        output_tokens=sum(call.output_tokens for call in calls),
        cost_usd=None if unpriced else sum(cost for cost in costs if cost is not None),
        unpriced_models=unpriced,
        latency_seconds=latency_seconds,
    )
