from typing import Any

from devassist.exceptions import LLMError, SchemaValidationError
from devassist.models import Analysis, Category, Issue, Review, RunMetrics, Severity
from evals.cases import Behavior, Difficulty, EvalCase, ExpectedIssue, Kind
from evals.scoring import RunRecord, is_matched, is_near_miss, reaches_model, score_run

EXPECTED = ExpectedIssue(category=Category.BUG, line_start=2, line_end=3, note="Wrong.")
PLANTED = EvalCase(
    id="bug-case",
    kind=Kind.PLANTED,
    difficulty=Difficulty.EASY,
    snippet="a = 1\nb = 2\nc = 3\n",
    expected_issues=[EXPECTED],
)
CLEAN = EvalCase(id="clean-case", kind=Kind.CLEAN, snippet="x = 1\n")
INJECTION = EvalCase(
    id="adversarial-injection",
    kind=Kind.ADVERSARIAL,
    snippet="a = 1\nb = 2\nc = 3\n",
    expected_issues=[
        ExpectedIssue(category=Category.SECURITY, line_start=2, line_end=3, note="Injection.")
    ],
)
NOT_PYTHON = EvalCase(
    id="adversarial-not-python",
    kind=Kind.ADVERSARIAL,
    behavior=Behavior.INPUT_ERROR,
    language="javascript",
    snippet="var x;\n",
)
BROKEN = EvalCase(
    id="adversarial-broken",
    kind=Kind.ADVERSARIAL,
    behavior=Behavior.REWRITE_PARSES,
    snippet="def f(\n",
)


def issue(category: str, start: int, end: int | None = None) -> Issue:
    """Return a reported issue in this category on these lines."""
    return Issue(
        severity=Severity.MEDIUM,
        category=Category(category),
        line_start=start,
        line_end=start if end is None else end,
        description="Something is wrong.",
    )


def analysis(*issues: Issue, code: str = "x = 1\n") -> Analysis:
    """Return a final answer reporting these issues."""
    return Analysis(
        summary="Sets x.", documentation="Sets x.", issues=list(issues), improved_code=code
    )


def metrics(
    rounds: int = 1,
    *,
    retries: int = 0,
    escalations: int = 0,
    reviews: int = 0,
    cost: float | None = 0.001,
) -> RunMetrics:
    """Return run metrics with these rounds, retries, escalations, reviews and cost."""
    return RunMetrics(
        rounds=rounds,
        models=["claude-haiku-5-5"] * rounds,
        escalations=escalations,
        retries=retries,
        reviews=reviews,
        input_tokens=100,
        output_tokens=50,
        cost_usd=cost,
        unpriced_models=[],
        latency_seconds=1.0,
    )


def record(**fields: Any) -> RunRecord:
    """Return a run record, with any fields replaced."""
    data: dict[str, Any] = {"config": "design", "case_id": "case", "run": 1, "latency_seconds": 2.0}
    data.update(fields)
    return RunRecord.model_validate(data)


def answered(result: Analysis, **fields: Any) -> RunRecord:
    """Return the record of a run that reached a final answer in one round."""
    return record(result=result, metrics=fields.pop("metrics", metrics()), **fields)


def failed(error_type: str = LLMError.__name__) -> RunRecord:
    """Return the record of a run that raised an error."""
    return record(error_type=error_type, error="The model call timed out.")


# Matching a reported issue to an expected one


def test_an_issue_matches_with_the_same_category_and_overlapping_lines() -> None:
    assert is_matched(EXPECTED, [issue("bug", 3, 5)])


def test_lines_that_touch_at_one_end_overlap() -> None:
    assert is_matched(EXPECTED, [issue("bug", 1, 2)])


def test_another_category_on_the_same_lines_does_not_match() -> None:
    assert not is_matched(EXPECTED, [issue("security", 2, 3)])


def test_the_same_category_on_other_lines_does_not_match() -> None:
    assert not is_matched(EXPECTED, [issue("bug", 4)])


def test_a_miss_only_on_category_is_a_near_miss() -> None:
    assert is_near_miss(EXPECTED, [issue("security", 2)])


def test_a_match_is_not_a_near_miss() -> None:
    assert not is_near_miss(EXPECTED, [issue("bug", 2), issue("security", 2)])


def test_an_issue_on_other_lines_is_not_a_near_miss() -> None:
    assert not is_near_miss(EXPECTED, [issue("security", 5)])


# Planted cases


def test_a_planted_case_scores_full_recall_when_its_issue_is_reported() -> None:
    score = score_run(PLANTED, answered(analysis(issue("bug", 2))))

    assert score.recall == 1.0
    assert score.false_alarm is None
    assert score.adversarial_pass is None


def test_a_planted_case_scores_zero_recall_when_its_issue_is_missed() -> None:
    score = score_run(PLANTED, answered(analysis(issue("security", 2))))

    assert score.recall == 0.0
    assert score.near_misses == 1


def test_recall_is_the_share_of_expected_issues_reported() -> None:
    second = ExpectedIssue(category=Category.PERFORMANCE, line_start=1, line_end=1, note="Slow.")
    case = PLANTED.model_copy(update={"expected_issues": [EXPECTED, second]})

    score = score_run(case, answered(analysis(issue("bug", 2))))

    assert score.recall == 0.5


def test_a_failed_run_scores_zero_recall_and_an_unknown_cost() -> None:
    score = score_run(PLANTED, failed())

    assert score.recall == 0.0
    assert score.failed
    assert score.cost_usd is None


# Clean cases


def test_a_clean_case_with_no_issues_is_not_a_false_alarm() -> None:
    score = score_run(CLEAN, answered(analysis()))

    assert score.false_alarm is False
    assert score.recall is None


def test_any_issue_on_a_clean_case_is_a_false_alarm() -> None:
    assert score_run(CLEAN, answered(analysis(issue("style", 1)))).false_alarm is True


def test_a_failed_run_on_a_clean_case_counts_as_a_false_alarm() -> None:
    assert score_run(CLEAN, failed()).false_alarm is True


# Adversarial cases


def test_the_injection_case_passes_only_if_its_issue_is_still_reported() -> None:
    reported = score_run(INJECTION, answered(analysis(issue("security", 3))))
    obeyed = score_run(INJECTION, answered(analysis()))

    assert reported.adversarial_pass is True
    assert obeyed.adversarial_pass is False
    assert reported.recall is None


def test_an_input_error_case_passes_when_rejected_before_any_model_call() -> None:
    score = score_run(
        NOT_PYTHON, record(input_error="Only Python is supported.", metrics=metrics(rounds=0))
    )

    assert score.adversarial_pass is True
    assert score.verified is None


def test_an_input_error_case_fails_when_the_model_reviews_it() -> None:
    assert score_run(NOT_PYTHON, answered(analysis())).adversarial_pass is False


def test_a_broken_snippet_passes_when_the_rewrite_parses() -> None:
    fixed = score_run(BROKEN, answered(analysis(code="def f():\n    pass\n")))
    still_broken = score_run(BROKEN, answered(analysis(code="def f(\n")))

    assert fixed.adversarial_pass is True
    assert still_broken.adversarial_pass is False


def test_a_failed_run_fails_an_adversarial_case() -> None:
    assert score_run(BROKEN, failed()).adversarial_pass is False


# Responses valid on the first try


def test_first_try_validity_comes_from_the_rounds_and_retries() -> None:
    score = score_run(PLANTED, answered(analysis(), metrics=metrics(rounds=3, retries=1)))

    assert (score.responses, score.first_try_valid) == (3, 2)


def test_an_answer_that_failed_validation_twice_counts_as_one_invalid_response() -> None:
    score = score_run(PLANTED, failed(SchemaValidationError.__name__))

    assert (score.responses, score.first_try_valid) == (1, 0)


def test_other_failures_add_no_responses() -> None:
    score = score_run(PLANTED, failed(LLMError.__name__))

    assert (score.responses, score.first_try_valid) == (0, 0)


def test_a_review_answer_counts_toward_first_try_validity() -> None:
    reviewed = answered(analysis(), metrics=metrics(rounds=2, reviews=1, retries=1))

    score = score_run(PLANTED, reviewed)

    assert (score.responses, score.first_try_valid) == (3, 2)


# The second review


def test_a_run_records_whether_its_review_flagged_missed_issues() -> None:
    flagged = answered(analysis(), review=Review(missed_issues=[issue("documentation", 1)]))
    clear = answered(analysis(), review=Review(missed_issues=[]))

    assert score_run(PLANTED, flagged).review_flagged is True
    assert score_run(PLANTED, clear).review_flagged is False


def test_a_run_without_a_review_has_no_review_flag() -> None:
    assert score_run(PLANTED, answered(analysis())).review_flagged is None


def test_records_saved_before_reviews_existed_still_load() -> None:
    saved = answered(analysis()).model_dump(mode="json", exclude={"review"})

    assert RunRecord.model_validate(saved).review is None


# Verification, rounds, cost and latency


def test_a_final_answer_without_warnings_is_verified() -> None:
    assert score_run(PLANTED, answered(analysis())).verified is True


def test_a_final_answer_with_warnings_is_not_verified() -> None:
    score = score_run(PLANTED, answered(analysis(), warnings=["new ruff finding B006"]))

    assert score.verified is False


def test_rounds_escalation_cost_and_latency_come_from_the_record() -> None:
    run_metrics = metrics(rounds=3, escalations=1, cost=0.02)

    score = score_run(PLANTED, answered(analysis(), metrics=run_metrics, latency_seconds=7.5))

    assert score.rounds == 3
    assert score.escalated
    assert score.cost_usd == 0.02
    assert score.latency_seconds == 7.5
    assert not score.failed


def test_only_cases_that_expect_an_input_error_skip_the_model() -> None:
    assert reaches_model(PLANTED)
    assert reaches_model(BROKEN)
    assert not reaches_model(NOT_PYTHON)


def test_a_record_round_trips_through_json() -> None:
    original = answered(analysis(issue("bug", 2)), warnings=["new ruff finding B006"])

    assert RunRecord.model_validate_json(original.model_dump_json()) == original
