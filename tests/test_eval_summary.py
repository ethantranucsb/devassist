from collections.abc import Sequence

import pytest

from devassist.config import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL
from devassist.models import Analysis, Category, Issue, RunMetrics, Severity
from evals.cases import Behavior, Difficulty, EvalCase, ExpectedIssue, Kind
from evals.configurations import BASELINE, CONFIGURATIONS, DESIGN
from evals.scoring import RunRecord
from evals.summary import (
    CSV_COLUMNS,
    ConfigSummary,
    CriterionResult,
    EvalSummary,
    Verdict,
    build_summary,
    case_rows,
    render_report,
    score_records,
    summarize_config,
)

RESAMPLES = 200  # enough for these small, exact cases, and quick
RUNS = (1, 2, 3)


def planted(case_id: str, difficulty: Difficulty) -> EvalCase:
    """Return a planted case whose expected bug is on line 1."""
    return EvalCase(
        id=case_id,
        kind=Kind.PLANTED,
        difficulty=difficulty,
        snippet="a = 1\nb = 2\n",
        expected_issues=[
            ExpectedIssue(category=Category.BUG, line_start=1, line_end=1, note="Wrong.")
        ],
    )


EASY = planted("bug-easy", Difficulty.EASY)
HARD = planted("bug-hard", Difficulty.HARD)
CLEAN = EvalCase(id="clean-case", kind=Kind.CLEAN, snippet="x = 1\n")
REJECTED = EvalCase(
    id="adversarial-rejected",
    kind=Kind.ADVERSARIAL,
    behavior=Behavior.INPUT_ERROR,
    language="javascript",
    snippet="var x;\n",
)
CASES = [EASY, HARD, CLEAN, REJECTED]


def issue(category: str) -> Issue:
    """Return a reported issue on line 1."""
    return Issue(
        severity=Severity.MEDIUM,
        category=Category(category),
        line_start=1,
        line_end=1,
        description="Something is wrong.",
    )


BUG = issue("bug")
STYLE = issue("style")


def run_metrics(
    *, rounds: int = 1, retries: int = 0, escalations: int = 0, cost: float, latency: float
) -> RunMetrics:
    """Return run metrics with these values."""
    return RunMetrics(
        rounds=rounds,
        models=[DEFAULT_MODEL] * rounds,
        escalations=escalations,
        retries=retries,
        input_tokens=100 * rounds,
        output_tokens=50 * rounds,
        cost_usd=cost,
        unpriced_models=[],
        latency_seconds=latency,
    )


def review(
    config: str,
    case: EvalCase,
    run: int,
    *,
    issues: Sequence[Issue] = (),
    cost: float = 0.001,
    latency: float = 2.0,
    rounds: int = 1,
    retries: int = 0,
    escalations: int = 0,
) -> RunRecord:
    """Return the record of a run that reached a final answer reporting these issues."""
    return RunRecord(
        config=config,
        case_id=case.id,
        run=run,
        latency_seconds=latency,
        result=Analysis(
            summary="Sets x.", documentation="Sets x.", issues=list(issues), improved_code="x = 1\n"
        ),
        metrics=run_metrics(
            rounds=rounds, retries=retries, escalations=escalations, cost=cost, latency=latency
        ),
    )


def rejected(config: str, run: int) -> RunRecord:
    """Return the record of a run whose input was rejected before any model call."""
    return RunRecord(
        config=config,
        case_id=REJECTED.id,
        run=run,
        latency_seconds=0.01,
        input_error="Only Python is supported, not 'javascript'.",
        metrics=run_metrics(rounds=0, cost=0.0, latency=0.01),
    )


def run_set(
    config: str,
    *,
    easy_found: int = 3,
    hard_found: int = 3,
    clean_alarms: int = 0,
    rejected_ok: int = 3,
    cost: float = 0.001,
    latency: float = 2.0,
    retries: int = 0,
) -> list[RunRecord]:
    """Return 3 runs of every case under one configuration.

    The counts say in how many of the 3 runs each thing happens: the planted
    bug is found, the clean case gets an issue, the bad input is rejected.
    """
    records = []
    for run in RUNS:
        common = {"cost": cost, "latency": latency, "retries": retries}
        records += [
            review(config, EASY, run, issues=[BUG] if run <= easy_found else [], **common),
            review(config, HARD, run, issues=[BUG] if run <= hard_found else [], **common),
            review(config, CLEAN, run, issues=[STYLE] if run <= clean_alarms else [], **common),
            rejected(config, run) if run <= rejected_ok else review(config, REJECTED, run),
        ]
    return records


def summary_of(
    *record_sets: list[RunRecord], configs: Sequence[str] = (DESIGN, BASELINE)
) -> EvalSummary:
    """Summarize the records of these configurations."""
    return build_summary(
        CASES,
        [record for records in record_sets for record in records],
        [CONFIGURATIONS[name] for name in configs],
        label="test",
        runs_per_case=len(RUNS),
        created_at="2026-10-18T00:00:00+00:00",
        resamples=RESAMPLES,
    )


def criteria(summary: EvalSummary) -> dict[int, CriterionResult]:
    """Return the summary's D8 criteria by number."""
    assert summary.criteria is not None
    return {criterion.number: criterion for criterion in summary.criteria}


def design_summary(records: list[RunRecord]) -> ConfigSummary:
    """Summarize the design's records alone."""
    return summarize_config(CONFIGURATIONS[DESIGN], CASES, score_records(CASES, records)[DESIGN])


# One configuration's results


def test_recall_averages_each_cases_runs_before_averaging_the_cases() -> None:
    # bug-easy is found in 1 of 3 runs and bug-hard in all 3, so recall is (1/3 + 1) / 2.
    result = design_summary(run_set(DESIGN, easy_found=1))

    assert result.recall == pytest.approx(2 / 3)
    assert result.recall_by_difficulty == {"easy": pytest.approx(1 / 3), "hard": 1.0}


def test_the_false_alarm_rate_is_the_share_of_clean_runs_with_any_issue() -> None:
    result = design_summary(run_set(DESIGN, clean_alarms=1))

    assert result.false_alarm_rate == pytest.approx(1 / 3)


def test_near_misses_are_counted_and_measures_without_cases_are_empty() -> None:
    result = design_summary([review(DESIGN, EASY, 1, issues=[STYLE])])

    assert result.near_misses == 1
    assert result.recall == 0.0
    assert result.false_alarm_rate is None
    assert result.adversarial_pass_rate is None


def test_latency_and_cost_leave_out_inputs_rejected_before_any_model_call() -> None:
    result = design_summary(run_set(DESIGN, latency=4.0, cost=0.002))

    assert result.latency_p50_seconds == 4.0
    assert result.latency_p95_seconds == 4.0
    assert result.mean_cost_usd == pytest.approx(0.002)
    assert result.total_cost_usd == pytest.approx(9 * 0.002)


def test_the_mean_cost_averages_the_cases() -> None:
    records = [review(DESIGN, EASY, run, cost=0.001) for run in RUNS]
    records += [review(DESIGN, HARD, run, cost=0.003) for run in RUNS]

    assert design_summary(records).mean_cost_usd == pytest.approx(0.002)


def test_first_try_validity_is_counted_over_responses() -> None:
    records = [review(DESIGN, EASY, 1, rounds=2, retries=1), review(DESIGN, EASY, 2)]

    assert design_summary(records).first_try_valid == pytest.approx(2 / 3)


def test_adversarial_failures_are_listed_by_case() -> None:
    result = design_summary(run_set(DESIGN, rejected_ok=2))

    assert result.adversarial_pass_rate == pytest.approx(2 / 3)
    assert result.adversarial_failures == {REJECTED.id: 1}


def test_rounds_and_escalations_leave_out_failed_runs_but_latency_keeps_them() -> None:
    timed_out = RunRecord(
        config=DESIGN,
        case_id=EASY.id,
        run=3,
        latency_seconds=120.0,
        error_type="LLMError",
        error="The model call timed out after 120 seconds.",
    )
    records = [review(DESIGN, EASY, 1, rounds=3, escalations=1), review(DESIGN, EASY, 2), timed_out]

    result = design_summary(records)

    assert result.mean_rounds == 2.0
    assert result.escalation_rate == 0.5
    assert result.failed_runs == 1
    assert result.latency_p95_seconds is not None and result.latency_p95_seconds > 100


# D8's bar


def test_the_design_passes_every_criterion_when_it_matches_sonnet_at_a_tenth_of_the_cost() -> None:
    summary = summary_of(run_set(DESIGN, cost=0.001), run_set(BASELINE, cost=0.01))

    verdicts = {number: criterion.verdict for number, criterion in criteria(summary).items()}
    assert verdicts == dict.fromkeys(range(1, 7), Verdict.PASS)
    assert criteria(summary)[5].value == pytest.approx(0.1)


def test_recall_fails_when_the_design_misses_what_sonnet_finds() -> None:
    summary = summary_of(run_set(DESIGN, easy_found=0, hard_found=0), run_set(BASELINE, cost=0.01))

    recall = criteria(summary)[1]
    assert recall.value == pytest.approx(-1.0)
    assert recall.verdict is Verdict.FAIL


def test_a_criterion_is_inconclusive_when_its_interval_contains_the_margin() -> None:
    # Each finds the bug the other misses: equal on average, but 2 cases can't tell them apart.
    summary = summary_of(run_set(DESIGN, easy_found=0), run_set(BASELINE, hard_found=0, cost=0.01))

    recall = criteria(summary)[1]
    assert recall.value == pytest.approx(0.0)
    assert recall.interval is not None
    assert recall.interval[0] < -0.10 < recall.interval[1]
    assert recall.verdict is Verdict.INCONCLUSIVE
    assert recall.point_estimate_passes is True


def test_false_alarms_fail_when_the_design_flags_clean_code_that_sonnet_leaves_alone() -> None:
    summary = summary_of(run_set(DESIGN, clean_alarms=3), run_set(BASELINE, cost=0.01))

    assert criteria(summary)[2].verdict is Verdict.FAIL


def test_first_try_validity_fails_below_95_percent() -> None:
    summary = summary_of(run_set(DESIGN, retries=1), run_set(BASELINE, cost=0.01))

    assert criteria(summary)[3].verdict is Verdict.FAIL


def test_latency_must_be_under_30_seconds_not_equal_to_it() -> None:
    summary = summary_of(run_set(DESIGN, latency=30.0), run_set(BASELINE, cost=0.01))

    assert criteria(summary)[4].verdict is Verdict.FAIL


def test_cost_fails_above_half_of_sonnets() -> None:
    summary = summary_of(run_set(DESIGN, cost=0.01), run_set(BASELINE, cost=0.01))

    cost = criteria(summary)[5]
    assert cost.value == pytest.approx(1.0)
    assert cost.verdict is Verdict.FAIL


def test_one_failed_adversarial_run_fails_the_adversarial_criterion() -> None:
    summary = summary_of(run_set(DESIGN, rejected_ok=2), run_set(BASELINE, cost=0.01))

    adversarial = criteria(summary)[6]
    assert adversarial.verdict is Verdict.FAIL
    assert adversarial.interval is None


def test_there_is_no_verdict_without_sonnet_alone() -> None:
    assert summary_of(run_set(DESIGN), configs=(DESIGN,)).criteria is None


def test_the_summary_records_how_it_was_produced() -> None:
    summary = summary_of(run_set(DESIGN), run_set(BASELINE))

    assert (summary.label, summary.cases, summary.runs_per_case) == ("test", 4, 3)
    assert summary.bootstrap_resamples == RESAMPLES
    assert summary.configs[DESIGN].model == DEFAULT_MODEL
    assert summary.configs[DESIGN].fallback_model == DEFAULT_FALLBACK_MODEL


# The CSV and the report


def test_the_csv_has_one_row_per_configuration_and_case() -> None:
    records = run_set(DESIGN, easy_found=1) + run_set(BASELINE)

    rows = case_rows(CASES, records, [CONFIGURATIONS[DESIGN], CONFIGURATIONS[BASELINE]])

    assert len(rows) == 8
    assert all(tuple(row) == CSV_COLUMNS for row in rows)
    easy = next(row for row in rows if row["config"] == DESIGN and row["case_id"] == EASY.id)
    assert easy["recall"] == pytest.approx(1 / 3)
    assert easy["false_alarm_rate"] is None
    assert easy["difficulty"] == "easy"


def test_the_report_shows_each_configuration_and_every_criterion_in_plain_ascii() -> None:
    report = render_report(summary_of(run_set(DESIGN, cost=0.001), run_set(BASELINE, cost=0.01)))

    assert DESIGN in report
    assert BASELINE in report
    for number in range(1, 7):
        assert f"\n{number}. " in report
    assert "-> pass" in report
    assert report.isascii()


def test_the_report_marks_an_inconclusive_criterion_with_its_point_estimate() -> None:
    summary = summary_of(run_set(DESIGN, easy_found=0), run_set(BASELINE, hard_found=0, cost=0.01))

    assert "inconclusive (point estimate passes)" in render_report(summary)


def test_the_report_explains_a_missing_verdict() -> None:
    assert "No verdict" in render_report(summary_of(run_set(DESIGN), configs=(DESIGN,)))
