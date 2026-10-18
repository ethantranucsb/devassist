"""Scoring one run against its case's answer key, by rule, with no human or model judging.

A run that raises an error gets the worst score on every quality measure, so
a configuration can't look better by failing (docs/decisions.md D16).
"""

import ast
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from devassist.exceptions import SchemaValidationError
from devassist.models import Analysis, Issue, RunMetrics
from evals.cases import Behavior, EvalCase, ExpectedIssue, Kind


class RunRecord(BaseModel):
    """Everything one run produced, saved as it is so the eval can be re-scored later.

    Attributes:
        config: The configuration's name.
        case_id: The case's id.
        run: Which run of the case this is, counting from 1.
        latency_seconds: Wall-clock time measured by the runner, failed runs included.
        input_error: Why devassist rejected the input, if it did.
        error_type: The class of the error that ended the run, if one did.
        error: That error's message.
        result: The final answer, if the run reached one.
        warnings: Problems the final answer still had after the last round.
        metrics: Rounds, tokens and cost; ``None`` if the run raised an error.
    """

    model_config = ConfigDict(frozen=True)

    config: str
    case_id: str
    run: int = Field(ge=1)
    latency_seconds: float = Field(ge=0)
    input_error: str | None = None
    error_type: str | None = None
    error: str | None = None
    result: Analysis | None = None
    warnings: list[str] = Field(default_factory=list)
    metrics: RunMetrics | None = None


@dataclass(frozen=True)
class RunScore:
    """One run's scores. A measure that doesn't apply to the case is ``None``.

    Attributes:
        recall: Planted cases: the share of expected issues the run reported.
        near_misses: Planted cases: expected issues missed only on category.
        false_alarm: Clean cases: whether the run reported any issue, or failed.
        adversarial_pass: Adversarial cases: whether the run did what the case requires.
        verified: Cases that reach the model: whether the final answer had no warnings.
        responses: Model answers checked against the schema.
        first_try_valid: How many of those passed without the retry.
        rounds: Model answers in the run; 0 if it failed, since that isn't reported.
        escalated: Whether any round used the fallback model.
        cost_usd: The run's cost, or ``None`` if unknown, as for a failed run.
        latency_seconds: Wall-clock time.
        failed: Whether the run raised an error.
    """

    recall: float | None
    near_misses: int
    false_alarm: bool | None
    adversarial_pass: bool | None
    verified: bool | None
    responses: int
    first_try_valid: int
    rounds: int
    escalated: bool
    cost_usd: float | None
    latency_seconds: float
    failed: bool


def reaches_model(case: EvalCase) -> bool:
    """Whether a correct run of the case calls the model, rather than rejecting the input."""
    return case.behavior is not Behavior.INPUT_ERROR


def _overlaps(expected: ExpectedIssue, issue: Issue) -> bool:
    """Whether the reported issue's lines share at least one line with the expected issue's."""
    return issue.line_start <= expected.line_end and expected.line_start <= issue.line_end


def is_matched(expected: ExpectedIssue, issues: Sequence[Issue]) -> bool:
    """Whether any reported issue has the expected category and overlaps the expected lines."""
    return any(
        issue.category == expected.category and _overlaps(expected, issue) for issue in issues
    )


def is_near_miss(expected: ExpectedIssue, issues: Sequence[Issue]) -> bool:
    """Whether the issue was missed only on category: something on its lines, filed differently."""
    return not is_matched(expected, issues) and any(_overlaps(expected, i) for i in issues)


def _parses(code: str) -> bool:
    """Whether the code parses as Python, read with ``ast`` and never run."""
    try:
        ast.parse(code)
    except (SyntaxError, RecursionError):
        return False
    return True


def _adversarial_pass(case: EvalCase, record: RunRecord) -> bool:
    """Whether the run did what the adversarial case requires."""
    if case.behavior is Behavior.INPUT_ERROR:
        # Rejected, and before any model call.
        return (
            record.input_error is not None
            and record.metrics is not None
            and record.metrics.rounds == 0
        )
    if record.result is None:
        return False
    if case.behavior is Behavior.REWRITE_PARSES:
        return _parses(record.result.improved_code)
    return all(is_matched(expected, record.result.issues) for expected in case.expected_issues)


def _responses(record: RunRecord) -> tuple[int, int]:
    """Count the model answers checked against the schema, and those valid on the first try."""
    if record.metrics is not None:
        return record.metrics.rounds, record.metrics.rounds - record.metrics.retries
    if record.error_type == SchemaValidationError.__name__:
        return 1, 0  # the answer that failed twice; earlier rounds weren't reported
    return 0, 0


def score_run(case: EvalCase, record: RunRecord) -> RunScore:
    """Score one run against its case's answer key.

    Args:
        case: The case that was run.
        record: What the run produced.

    Returns:
        The run's scores, with ``None`` for measures that don't apply to the case.
    """
    answered = record.result is not None
    issues = record.result.issues if record.result is not None else []
    planted = case.kind is Kind.PLANTED
    responses, first_try_valid = _responses(record)
    metrics = record.metrics
    return RunScore(
        recall=(
            sum(is_matched(expected, issues) for expected in case.expected_issues)
            / len(case.expected_issues)
            if planted
            else None
        ),
        near_misses=(
            sum(is_near_miss(expected, issues) for expected in case.expected_issues)
            if planted
            else 0
        ),
        false_alarm=(not answered or bool(issues)) if case.kind is Kind.CLEAN else None,
        adversarial_pass=(
            _adversarial_pass(case, record) if case.kind is Kind.ADVERSARIAL else None
        ),
        verified=(answered and not record.warnings) if reaches_model(case) else None,
        responses=responses,
        first_try_valid=first_try_valid,
        rounds=metrics.rounds if metrics is not None else 0,
        escalated=metrics is not None and metrics.escalations > 0,
        cost_usd=metrics.cost_usd if metrics is not None else None,
        latency_seconds=record.latency_seconds,
        failed=record.error is not None,
    )
