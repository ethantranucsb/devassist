import pytest

from devassist.config import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL
from devassist.metrics import price_for, summarize
from devassist.models import Analysis, Attempt

HAIKU = DEFAULT_MODEL
SONNET = DEFAULT_FALLBACK_MODEL
ANALYSIS = Analysis(
    summary="Adds.", documentation="`add` adds.", issues=[], improved_code="x = 1\n"
)


def attempt(
    round_number: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    escalated: bool = False,
    retried: bool = False,
) -> Attempt:
    """Return a recorded round with the given model and token counts."""
    return Attempt(
        round=round_number,
        model=model,
        analysis=ANALYSIS,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        escalated=escalated,
        retried=retried,
    )


# Two Haiku rounds, then an escalation to Sonnet: the worked example.
MIXED_RUN = [
    attempt(1, HAIKU, 2_000, 1_000),
    attempt(2, HAIKU, 2_000, 1_000),
    attempt(3, SONNET, 2_500, 1_000, escalated=True),
]


def test_a_mixed_run_prices_each_round_at_its_own_models_rate() -> None:
    metrics = summarize(MIXED_RUN, latency_seconds=18.4)

    assert metrics.cost_usd == pytest.approx(0.0164)


def test_totals_count_every_round() -> None:
    metrics = summarize(MIXED_RUN, latency_seconds=18.4)

    assert metrics.rounds == 3
    assert metrics.models == [HAIKU, HAIKU, SONNET]
    assert metrics.escalations == 1
    assert metrics.input_tokens == 6_500
    assert metrics.output_tokens == 3_000
    assert metrics.latency_seconds == 18.4
    assert metrics.unpriced_models == []


def test_retries_are_counted_per_round() -> None:
    attempts = [
        attempt(1, HAIKU, 100, 50, retried=True),
        attempt(2, HAIKU, 100, 50),
        attempt(3, SONNET, 100, 50, escalated=True, retried=True),
    ]

    assert summarize(attempts, latency_seconds=1.0).retries == 2


def test_a_run_without_retries_counts_none() -> None:
    assert summarize(MIXED_RUN, latency_seconds=18.4).retries == 0


def test_a_dated_model_id_is_priced_by_its_prefix() -> None:
    assert price_for(f"{HAIKU}-20261001") == price_for(HAIKU)


def test_a_name_that_only_starts_the_same_is_not_matched() -> None:
    assert price_for(f"{HAIKU}0") is None


def test_an_unknown_model_makes_the_whole_cost_unknown() -> None:
    attempts = [attempt(1, HAIKU, 100, 50), attempt(2, "claude-test", 100, 50)]

    metrics = summarize(attempts, latency_seconds=1.0)

    assert metrics.cost_usd is None
    assert metrics.unpriced_models == ["claude-test"]


def test_a_run_with_no_rounds_costs_nothing() -> None:
    metrics = summarize([], latency_seconds=0.01)

    assert metrics.rounds == 0
    assert metrics.models == []
    assert metrics.cost_usd == 0.0
