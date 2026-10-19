"""Turning scored runs into each configuration's results and D8's verdict.

Each case's runs are averaged first, so the case is the unit, and the design
is compared with Sonnet alone by a paired bootstrap over cases (D8, D16).
"""

import math
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel

from devassist.config import DEFAULT_REVIEW_MODEL
from evals.cases import Difficulty, EvalCase, Kind
from evals.configurations import BASELINE, DESIGN, Configuration
from evals.scoring import RunRecord, RunScore, reaches_model, score_run
from evals.stats import bootstrap_interval, mean, percentile

# D8's bar, set Oct 17, 2026, before any results existed. Shares are 0 to 1.
RECALL_MARGIN = -0.10  # the design's recall minus Sonnet alone's
FALSE_ALARM_MARGIN = 0.10  # the design's false-alarm rate minus Sonnet alone's
FIRST_TRY_VALID_MIN = 0.95
LATENCY_P95_LIMIT_SECONDS = 30.0  # p95 must be under this
COST_RATIO_MAX = 0.50  # the design's mean cost per snippet over Sonnet alone's
ADVERSARIAL_PASS_RATE = 1.0  # every adversarial case, in every run

MEDIAN = 0.5
P95 = 0.95
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261017  # the day the bar was set; any fixed value would do
CONFIDENCE = 0.95

type CaseScores = dict[str, list[RunScore]]  # case id to its runs' scores


class Verdict(StrEnum):
    """How one criterion of the bar came out."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"  # the interval contains the threshold


class Unit(StrEnum):
    """How a criterion's numbers are shown."""

    POINTS = "points"  # a difference between two shares, in percentage points
    PERCENT = "percent"
    SECONDS = "seconds"


class CriterionResult(BaseModel):
    """One criterion of D8's bar, judged.

    Attributes:
        number: Its number in D8.
        name: A short name.
        rule: What it requires, in words.
        unit: How its numbers are shown.
        value: The point estimate, or ``None`` if no case measures it.
        threshold: The value it must reach.
        interval: The bootstrap interval, or ``None`` for a criterion without one.
        point_estimate_passes: Whether the point estimate meets the rule.
        verdict: Pass or fail by the point estimate, or inconclusive if the
            interval contains the threshold.
    """

    number: int
    name: str
    rule: str
    unit: Unit
    value: float | None
    threshold: float
    interval: tuple[float, float] | None
    point_estimate_passes: bool | None
    verdict: Verdict


class ConfigSummary(BaseModel):
    """One configuration's results. A measure no case covers is ``None``.

    Attributes:
        name: The configuration.
        model: Its model before the escalation round.
        fallback_model: Its model from the escalation round on.
        review_model: The model of its second review, or ``None`` without one.
        runs: Runs of every case.
        failed_runs: Runs that raised an error.
        recall: Mean recall over planted cases.
        recall_by_difficulty: The same, for each difficulty present.
        near_misses: Expected issues missed only on category, over all runs.
        false_alarm_rate: Mean share of runs with any issue, over clean cases.
        first_try_valid: The share of model answers valid without the retry.
        verified_rate: The share of runs reaching the model whose answer had no warnings.
        adversarial_pass_rate: The share of adversarial runs that passed.
        adversarial_failures: Failed runs by adversarial case.
        mean_rounds: Mean rounds per run, over answered runs reaching the model.
        escalation_rate: The share of those runs that escalated.
        reviewed_runs: Runs reaching the model that had a second review.
        review_flag_rate: The share of those whose review found missed issues.
        mean_cost_usd: Mean cost per snippet, over cases reaching the model.
        total_cost_usd: Every known cost, added up.
        latency_p50_seconds: Median latency over runs reaching the model.
        latency_p95_seconds: p95 latency over the same runs.
    """

    name: str
    model: str
    fallback_model: str
    review_model: str | None = None
    runs: int
    failed_runs: int
    recall: float | None
    recall_by_difficulty: dict[str, float]
    near_misses: int
    false_alarm_rate: float | None
    first_try_valid: float | None
    verified_rate: float | None
    adversarial_pass_rate: float | None
    adversarial_failures: dict[str, int]
    mean_rounds: float | None
    escalation_rate: float | None
    reviewed_runs: int = 0
    review_flag_rate: float | None = None
    mean_cost_usd: float | None
    total_cost_usd: float
    latency_p50_seconds: float | None
    latency_p95_seconds: float | None


class EvalSummary(BaseModel):
    """The eval's results: each configuration's, and D8's verdict on the design.

    Attributes:
        label: ``smoke`` or ``full``.
        created_at: When the summary was made, in ISO 8601.
        cases: How many cases ran.
        runs_per_case: Runs of each case under each configuration.
        bootstrap_resamples: Resamples behind each interval.
        bootstrap_seed: The seed that makes the intervals reproducible.
        configs: Each configuration's results, in the order they were asked for.
        criteria: D8's six criteria, or ``None`` unless the design and Sonnet
            alone both ran.
    """

    label: str
    created_at: str
    cases: int
    runs_per_case: int
    bootstrap_resamples: int
    bootstrap_seed: int
    configs: dict[str, ConfigSummary]
    criteria: list[CriterionResult] | None


def score_records(cases: Sequence[EvalCase], records: Sequence[RunRecord]) -> dict[str, CaseScores]:
    """Score every record, grouped by configuration and then by case."""
    by_id = {case.id: case for case in cases}
    table: dict[str, CaseScores] = {}
    for record in records:
        runs = table.setdefault(record.config, {}).setdefault(record.case_id, [])
        runs.append(score_run(by_id[record.case_id], record))
    return table


def case_recall(runs: Sequence[RunScore]) -> float:
    """A planted case's recall, averaged over its runs."""
    return mean([run.recall for run in runs if run.recall is not None])


def case_false_alarm_rate(runs: Sequence[RunScore]) -> float:
    """A clean case's share of runs with a false alarm."""
    return mean([float(run.false_alarm) for run in runs if run.false_alarm is not None])


def case_cost(runs: Sequence[RunScore]) -> float | None:
    """A case's mean cost over the runs whose cost is known, or ``None`` if none is."""
    costs = [run.cost_usd for run in runs if run.cost_usd is not None]
    return mean(costs) if costs else None


def _mean_or_none(values: Sequence[float]) -> float | None:
    """Average the values, or return ``None`` if there are none."""
    return mean(values) if values else None


def _share(flags: Sequence[bool]) -> float | None:
    """Return the share of true flags, or ``None`` if there are none."""
    return _mean_or_none([float(flag) for flag in flags])


def _cases(
    cases: Sequence[EvalCase], scores: CaseScores, keep: Callable[[EvalCase], bool]
) -> list[EvalCase]:
    """The cases that were run and that ``keep`` selects, in dataset order."""
    return [case for case in cases if case.id in scores and keep(case)]


def _is_planted(case: EvalCase) -> bool:
    return case.kind is Kind.PLANTED


def _is_clean(case: EvalCase) -> bool:
    return case.kind is Kind.CLEAN


def _is_adversarial(case: EvalCase) -> bool:
    return case.kind is Kind.ADVERSARIAL


def summarize_config(
    configuration: Configuration, cases: Sequence[EvalCase], scores: CaseScores
) -> ConfigSummary:
    """Summarize one configuration's scored runs.

    Args:
        configuration: The configuration.
        cases: Every case, in dataset order.
        scores: The configuration's scores, by case id.

    Returns:
        The configuration's results.
    """
    planted = _cases(cases, scores, _is_planted)
    adversarial = _cases(cases, scores, _is_adversarial)
    all_runs = [run for runs in scores.values() for run in runs]
    model_cases = _cases(cases, scores, reaches_model)
    model_runs = [run for case in model_cases for run in scores[case.id]]
    answered_runs = [run for run in model_runs if not run.failed]
    recalls = {case.id: case_recall(scores[case.id]) for case in planted}
    by_difficulty = {}
    for difficulty in Difficulty:
        values = [recalls[case.id] for case in planted if case.difficulty is difficulty]
        if values:
            by_difficulty[difficulty.value] = mean(values)
    responses = sum(run.responses for run in all_runs)
    failures = {
        case.id: sum(not run.adversarial_pass for run in scores[case.id]) for case in adversarial
    }
    costs = [cost for case in model_cases if (cost := case_cost(scores[case.id])) is not None]
    latencies = [run.latency_seconds for run in model_runs]
    review_flags = [run.review_flagged for run in model_runs if run.review_flagged is not None]
    return ConfigSummary(
        name=configuration.name,
        model=configuration.model,
        fallback_model=configuration.fallback_model,
        review_model=DEFAULT_REVIEW_MODEL if configuration.review else None,
        runs=len(all_runs),
        failed_runs=sum(run.failed for run in all_runs),
        recall=_mean_or_none(list(recalls.values())),
        recall_by_difficulty=by_difficulty,
        near_misses=sum(run.near_misses for run in all_runs),
        false_alarm_rate=_mean_or_none(
            [case_false_alarm_rate(scores[case.id]) for case in _cases(cases, scores, _is_clean)]
        ),
        first_try_valid=sum(run.first_try_valid for run in all_runs) / responses
        if responses
        else None,
        verified_rate=_share([run.verified for run in model_runs if run.verified is not None]),
        adversarial_pass_rate=_share(
            [bool(run.adversarial_pass) for case in adversarial for run in scores[case.id]]
        ),
        adversarial_failures={case_id: count for case_id, count in failures.items() if count},
        mean_rounds=_mean_or_none([float(run.rounds) for run in answered_runs]),
        escalation_rate=_share([run.escalated for run in answered_runs]),
        reviewed_runs=len(review_flags),
        review_flag_rate=_share(review_flags),
        mean_cost_usd=_mean_or_none(costs),
        total_cost_usd=math.fsum(run.cost_usd for run in all_runs if run.cost_usd is not None),
        latency_p50_seconds=percentile(latencies, MEDIAN) if latencies else None,
        latency_p95_seconds=percentile(latencies, P95) if latencies else None,
    )


def _judge(
    *,
    number: int,
    name: str,
    rule: str,
    unit: Unit,
    threshold: float,
    passes: Callable[[float], bool],
    size: int,
    statistic: Callable[[Sequence[int]], float],
    resamples: int,
) -> CriterionResult:
    """Judge one criterion from a statistic over case indices.

    Pass or fail is decided on the point estimate. If the bootstrap interval
    contains the threshold, 30 cases can't settle it, and it's inconclusive.
    """
    if size == 0:
        return CriterionResult(
            number=number,
            name=name,
            rule=rule,
            unit=unit,
            value=None,
            threshold=threshold,
            interval=None,
            point_estimate_passes=None,
            verdict=Verdict.INCONCLUSIVE,
        )
    value = statistic(range(size))
    interval = bootstrap_interval(
        size, statistic, resamples=resamples, seed=BOOTSTRAP_SEED, confidence=CONFIDENCE
    )
    point_estimate_passes = passes(value)
    if interval[0] < threshold < interval[1]:
        verdict = Verdict.INCONCLUSIVE
    else:
        verdict = Verdict.PASS if point_estimate_passes else Verdict.FAIL
    return CriterionResult(
        number=number,
        name=name,
        rule=rule,
        unit=unit,
        value=value,
        threshold=threshold,
        interval=interval,
        point_estimate_passes=point_estimate_passes,
        verdict=verdict,
    )


def _adversarial_criterion(cases: Sequence[EvalCase], design: CaseScores) -> CriterionResult:
    """Judge criterion 6. It has no interval: a single failed run fails it."""
    passes = [bool(run.adversarial_pass) for case in cases for run in design[case.id]]
    value = _share(passes)
    if value is None:
        point_estimate_passes, verdict = None, Verdict.INCONCLUSIVE
    else:
        point_estimate_passes = value >= ADVERSARIAL_PASS_RATE
        verdict = Verdict.PASS if point_estimate_passes else Verdict.FAIL
    return CriterionResult(
        number=6,
        name="Adversarial cases",
        rule="every one passes in every run",
        unit=Unit.PERCENT,
        value=value,
        threshold=ADVERSARIAL_PASS_RATE,
        interval=None,
        point_estimate_passes=point_estimate_passes,
        verdict=verdict,
    )


def judge_bar(
    cases: Sequence[EvalCase],
    design: CaseScores,
    baseline: CaseScores,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> list[CriterionResult]:
    """Judge the design against D8's six criteria.

    Args:
        cases: Every case, in dataset order.
        design: The design's scores, by case id.
        baseline: Sonnet alone's scores, by case id.
        resamples: Resamples behind each bootstrap interval.

    Returns:
        The six criteria, in D8's order.
    """
    shared = [case for case in cases if case.id in design and case.id in baseline]
    recall_gaps = [
        case_recall(design[case.id]) - case_recall(baseline[case.id])
        for case in shared
        if _is_planted(case)
    ]
    alarm_gaps = [
        case_false_alarm_rate(design[case.id]) - case_false_alarm_rate(baseline[case.id])
        for case in shared
        if _is_clean(case)
    ]
    validity = []  # (valid on first try, answers checked) for each case with any answers
    for case in cases:
        if case.id in design:
            responses = sum(run.responses for run in design[case.id])
            if responses:
                validity.append((sum(run.first_try_valid for run in design[case.id]), responses))
    latencies = [
        [run.latency_seconds for run in design[case.id]]
        for case in _cases(cases, design, reaches_model)
    ]
    costs = []
    for case in shared:
        design_cost, baseline_cost = case_cost(design[case.id]), case_cost(baseline[case.id])
        if reaches_model(case) and design_cost is not None and baseline_cost is not None:
            costs.append((design_cost, baseline_cost))

    def recall_gap(indices: Sequence[int]) -> float:
        return mean([recall_gaps[i] for i in indices])

    def alarm_gap(indices: Sequence[int]) -> float:
        return mean([alarm_gaps[i] for i in indices])

    def first_try_share(indices: Sequence[int]) -> float:
        return sum(validity[i][0] for i in indices) / sum(validity[i][1] for i in indices)

    def latency_p95(indices: Sequence[int]) -> float:
        return percentile([latency for i in indices for latency in latencies[i]], P95)

    def cost_ratio(indices: Sequence[int]) -> float:
        return sum(costs[i][0] for i in indices) / sum(costs[i][1] for i in indices)

    return [
        _judge(
            number=1,
            name="Issue recall",
            rule=f"no more than {-RECALL_MARGIN * 100:.0f} points below Sonnet alone",
            unit=Unit.POINTS,
            threshold=RECALL_MARGIN,
            passes=lambda value: value >= RECALL_MARGIN,
            size=len(recall_gaps),
            statistic=recall_gap,
            resamples=resamples,
        ),
        _judge(
            number=2,
            name="False alarms",
            rule=f"no more than {FALSE_ALARM_MARGIN * 100:.0f} points above Sonnet alone",
            unit=Unit.POINTS,
            threshold=FALSE_ALARM_MARGIN,
            passes=lambda value: value <= FALSE_ALARM_MARGIN,
            size=len(alarm_gaps),
            statistic=alarm_gap,
            resamples=resamples,
        ),
        _judge(
            number=3,
            name="Valid on first try",
            rule=f"at least {FIRST_TRY_VALID_MIN:.0%} of model answers",
            unit=Unit.PERCENT,
            threshold=FIRST_TRY_VALID_MIN,
            passes=lambda value: value >= FIRST_TRY_VALID_MIN,
            size=len(validity),
            statistic=first_try_share,
            resamples=resamples,
        ),
        _judge(
            number=4,
            name="Latency",
            rule=f"p95 under {LATENCY_P95_LIMIT_SECONDS:.0f} s",
            unit=Unit.SECONDS,
            threshold=LATENCY_P95_LIMIT_SECONDS,
            passes=lambda value: value < LATENCY_P95_LIMIT_SECONDS,
            size=len(latencies),
            statistic=latency_p95,
            resamples=resamples,
        ),
        _judge(
            number=5,
            name="Cost",
            rule=f"mean cost per snippet at most {COST_RATIO_MAX:.0%} of Sonnet alone's",
            unit=Unit.PERCENT,
            threshold=COST_RATIO_MAX,
            passes=lambda value: value <= COST_RATIO_MAX,
            size=len(costs),
            statistic=cost_ratio,
            resamples=resamples,
        ),
        _adversarial_criterion(_cases(cases, design, _is_adversarial), design),
    ]


def build_summary(
    cases: Sequence[EvalCase],
    records: Sequence[RunRecord],
    configurations: Sequence[Configuration],
    *,
    label: str,
    runs_per_case: int,
    created_at: str,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> EvalSummary:
    """Score the records and summarize them, with D8's verdict when it can be judged.

    Args:
        cases: The cases that ran, in dataset order.
        records: Every run's record.
        configurations: The configurations that ran, in the order to report them.
        label: ``smoke`` or ``full``.
        runs_per_case: Runs of each case under each configuration.
        created_at: When the eval finished, in ISO 8601.
        resamples: Resamples behind each bootstrap interval.

    Returns:
        The summary. Its criteria are ``None`` unless the design and Sonnet
        alone both ran.
    """
    table = score_records(cases, records)
    configs = {
        configuration.name: summarize_config(
            configuration, cases, table.get(configuration.name, {})
        )
        for configuration in configurations
    }
    criteria = None
    if DESIGN in table and BASELINE in table:
        criteria = judge_bar(cases, table[DESIGN], table[BASELINE], resamples=resamples)
    return EvalSummary(
        label=label,
        created_at=created_at,
        cases=len(cases),
        runs_per_case=runs_per_case,
        bootstrap_resamples=resamples,
        bootstrap_seed=BOOTSTRAP_SEED,
        configs=configs,
        criteria=criteria,
    )


CSV_COLUMNS = (
    "config",
    "case_id",
    "kind",
    "difficulty",
    "runs",
    "failed_runs",
    "recall",
    "false_alarm_rate",
    "adversarial_pass_rate",
    "first_try_valid",
    "verified_rate",
    "mean_rounds",
    "escalation_rate",
    "mean_cost_usd",
    "mean_latency_seconds",
)

type CsvRow = dict[str, str | int | float | None]


def case_rows(
    cases: Sequence[EvalCase],
    records: Sequence[RunRecord],
    configurations: Sequence[Configuration],
) -> list[CsvRow]:
    """One row per configuration and case, each case's runs averaged, for a spreadsheet.

    Args:
        cases: The cases that ran, in dataset order.
        records: Every run's record.
        configurations: The configurations that ran, in the order to list them.

    Returns:
        The rows, with keys in ``CSV_COLUMNS`` order and ``None`` where a
        measure doesn't apply.
    """
    table = score_records(cases, records)
    rows: list[CsvRow] = []
    for configuration in configurations:
        scores = table.get(configuration.name, {})
        for case in [case for case in cases if case.id in scores]:
            runs = scores[case.id]
            responses = sum(run.responses for run in runs)
            answered = [run for run in runs if not run.failed]
            rows.append(
                {
                    "config": configuration.name,
                    "case_id": case.id,
                    "kind": case.kind.value,
                    "difficulty": case.difficulty.value if case.difficulty else None,
                    "runs": len(runs),
                    "failed_runs": sum(run.failed for run in runs),
                    "recall": case_recall(runs) if _is_planted(case) else None,
                    "false_alarm_rate": case_false_alarm_rate(runs) if _is_clean(case) else None,
                    "adversarial_pass_rate": (
                        _share([bool(run.adversarial_pass) for run in runs])
                        if _is_adversarial(case)
                        else None
                    ),
                    "first_try_valid": (
                        sum(run.first_try_valid for run in runs) / responses if responses else None
                    ),
                    "verified_rate": _share([r.verified for r in runs if r.verified is not None]),
                    "mean_rounds": _mean_or_none([float(run.rounds) for run in answered]),
                    "escalation_rate": _share([run.escalated for run in answered]),
                    "mean_cost_usd": case_cost(runs),
                    "mean_latency_seconds": mean([run.latency_seconds for run in runs]),
                }
            )
    return rows


def format_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Lay out a plain-text table with left-aligned, space-padded columns."""
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in [header, *rows]
    ]


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _dollars(value: float | None) -> str:
    return "-" if value is None else f"${value:.5f}"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} s"


def _show(value: float, unit: Unit) -> str:
    """Show a criterion's number in its unit."""
    if unit is Unit.POINTS:
        return f"{value * 100:+.1f} points"
    if unit is Unit.SECONDS:
        return _seconds(value)
    return _percent(value)


def _criterion_line(criterion: CriterionResult) -> str:
    """Describe one criterion's result on one line."""
    if criterion.value is None:
        return f"{criterion.number}. {criterion.name}: no cases measure it -> {criterion.verdict}"
    line = f"{criterion.number}. {criterion.name}: {_show(criterion.value, criterion.unit)}"
    if criterion.interval is not None:
        low, high = (_show(end, criterion.unit) for end in criterion.interval)
        line += f" ({CONFIDENCE:.0%} CI {low} to {high})"
    line += f"; needs {criterion.rule} -> {criterion.verdict}"
    if criterion.verdict is Verdict.INCONCLUSIVE and criterion.point_estimate_passes is not None:
        line += f" (point estimate {'passes' if criterion.point_estimate_passes else 'fails'})"
    return line


def render_report(summary: EvalSummary) -> str:
    """Render the summary as plain ASCII text for the terminal.

    Args:
        summary: The eval's results.

    Returns:
        A results table per configuration, recall by difficulty, and D8's verdict.
    """
    configs = list(summary.configs.values())
    lines = [
        f"devassist eval ({summary.label}): {summary.cases} cases x {summary.runs_per_case} runs"
    ]
    header = ["config", "recall", "false alarms", "first try", "verified", "adversarial"]
    header += ["rounds", "escalated", "review flagged", "cost/snippet", "p50", "p95", "failed"]
    rows = [
        [
            c.name,
            _percent(c.recall),
            _percent(c.false_alarm_rate),
            _percent(c.first_try_valid),
            _percent(c.verified_rate),
            _percent(c.adversarial_pass_rate),
            "-" if c.mean_rounds is None else f"{c.mean_rounds:.2f}",
            _percent(c.escalation_rate),
            _percent(c.review_flag_rate),
            _dollars(c.mean_cost_usd),
            _seconds(c.latency_p50_seconds),
            _seconds(c.latency_p95_seconds),
            str(c.failed_runs),
        ]
        for c in configs
    ]
    lines += ["", *format_table(header, rows)]
    difficulties = [difficulty.value for difficulty in Difficulty]
    rows = [
        [c.name, *(_percent(c.recall_by_difficulty.get(d)) for d in difficulties)] for c in configs
    ]
    lines += ["", "Recall by difficulty:", *format_table(["config", *difficulties], rows)]
    near_misses = ", ".join(f"{c.name} {c.near_misses}" for c in configs)
    lines += ["", f"Near misses (right lines, another category): {near_misses}"]
    for c in configs:
        for case_id, count in c.adversarial_failures.items():
            lines.append(f"Adversarial failure: {c.name} {case_id} in {count} run(s)")
    total = math.fsum(c.total_cost_usd for c in configs)
    lines.append(f"Total cost: {_dollars(total)}")
    lines += ["", "D8 bar, the design against Sonnet alone:"]
    if summary.criteria is None:
        lines.append("No verdict: it needs the design and Sonnet alone in the same eval.")
    else:
        lines += [_criterion_line(criterion) for criterion in summary.criteria]
    return "\n".join(lines)
