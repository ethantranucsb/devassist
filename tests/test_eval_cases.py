import ast
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from devassist.analyzers import BanditAnalyzer, RuffAnalyzer
from devassist.config import MAX_SNIPPET_CHARS
from devassist.models import Category
from evals.cases import Behavior, Difficulty, EvalCase, Kind, load_cases

CASES = load_cases()
PLANTED = [case for case in CASES if case.kind is Kind.PLANTED]
CLEAN = [case for case in CASES if case.kind is Kind.CLEAN]
PYTHON_TO_PARSE = [
    case for case in CASES if case.language == "python" and case.behavior is Behavior.REVIEW
]


def case_id(case: EvalCase) -> str:
    """Name each parametrized test after its case."""
    return case.id


def planted(**overrides: Any) -> dict[str, Any]:
    """Return valid planted-case data, with any fields replaced by the overrides."""
    data: dict[str, Any] = {
        "id": "bug-example",
        "kind": "planted",
        "difficulty": "easy",
        "snippet": "def f(x=[]):\n    return x\n",
        "expected_issues": [
            {"category": "bug", "line_start": 1, "line_end": 2, "note": "Mutable default."}
        ],
    }
    data.update(overrides)
    return data


# The dataset itself


def test_the_case_counts_match_the_plan() -> None:
    assert Counter(case.kind for case in CASES) == {
        Kind.PLANTED: 18,
        Kind.CLEAN: 8,
        Kind.ADVERSARIAL: 4,
    }


def test_case_ids_are_unique() -> None:
    ids = [case.id for case in CASES]

    assert len(ids) == len(set(ids))


def test_planted_issues_cover_the_four_categories_as_planned() -> None:
    categories = Counter(issue.category for case in PLANTED for issue in case.expected_issues)

    assert categories == {
        Category.BUG: 6,
        Category.SECURITY: 5,
        Category.PERFORMANCE: 4,
        Category.DOCUMENTATION: 3,
    }


def test_planted_cases_include_every_difficulty() -> None:
    assert {case.difficulty for case in PLANTED} == set(Difficulty)


@pytest.mark.parametrize("case", PYTHON_TO_PARSE, ids=case_id)
def test_snippets_reviewed_as_python_parse(case: EvalCase) -> None:
    ast.parse(case.code)


def test_the_invalid_syntax_cases_really_are_invalid() -> None:
    broken = [case for case in CASES if case.behavior is Behavior.REWRITE_PARSES]

    assert broken
    for case in broken:
        with pytest.raises(SyntaxError):
            ast.parse(case.code)


@pytest.mark.parametrize("case", CLEAN, ids=case_id)
def test_clean_controls_have_no_static_findings(case: EvalCase) -> None:
    assert RuffAnalyzer().analyze(case.code) == []
    assert BanditAnalyzer().analyze(case.code) == []


def test_input_error_cases_really_are_rejected_inputs() -> None:
    rejected = [case for case in CASES if case.behavior is Behavior.INPUT_ERROR]

    assert {case.language for case in rejected} == {"python", "javascript"}
    for case in rejected:
        if case.language == "python":
            assert len(case.code) > MAX_SNIPPET_CHARS


def test_the_smoke_subset_has_twelve_cases_of_every_kind() -> None:
    smoke = [case for case in CASES if case.smoke]

    assert len(smoke) == 12
    assert {case.kind for case in smoke} == set(Kind)


# The schema's rules


def test_valid_case_data_loads() -> None:
    case = EvalCase.model_validate(planted())

    assert case.expected_issues[0].category is Category.BUG
    assert case.behavior is Behavior.REVIEW
    assert case.language == "python"


def test_a_planted_case_needs_an_expected_issue() -> None:
    with pytest.raises(ValidationError, match="expected issue"):
        EvalCase.model_validate(planted(expected_issues=[]))


def test_a_planted_case_needs_a_difficulty() -> None:
    with pytest.raises(ValidationError, match="difficulty"):
        EvalCase.model_validate(planted(difficulty=None))


def test_a_clean_case_cannot_expect_issues() -> None:
    with pytest.raises(ValidationError, match="clean"):
        EvalCase.model_validate(planted(kind="clean", difficulty=None))


def test_only_adversarial_cases_may_expect_another_behavior() -> None:
    with pytest.raises(ValidationError, match="adversarial"):
        EvalCase.model_validate(planted(behavior="input_error"))


def test_an_expected_issue_must_fit_inside_the_snippet() -> None:
    issue = {"category": "bug", "line_start": 1, "line_end": 9, "note": "Past the end."}

    with pytest.raises(ValidationError, match="line 9"):
        EvalCase.model_validate(planted(expected_issues=[issue]))


def test_an_expected_issue_cannot_end_before_it_starts() -> None:
    issue = {"category": "bug", "line_start": 2, "line_end": 1, "note": "Backwards."}

    with pytest.raises(ValidationError, match="line_end"):
        EvalCase.model_validate(planted(expected_issues=[issue]))


def test_a_misspelled_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="expected_issue"):
        EvalCase.model_validate({**planted(), "expected_issue": []})


def test_ids_are_lowercase_words_joined_by_hyphens() -> None:
    with pytest.raises(ValidationError, match="id"):
        EvalCase.model_validate(planted(id="Bug Example"))


def test_repeat_builds_the_code_from_the_snippet() -> None:
    data = planted(kind="clean", difficulty=None, snippet="x = 1\n", repeat=3, expected_issues=[])

    case = EvalCase.model_validate(data)

    assert case.code == "x = 1\nx = 1\nx = 1\n"


# Loading


def test_load_cases_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(EvalCase.model_validate(planted()).model_dump_json() + "\n\n", encoding="utf-8")

    assert [case.id for case in load_cases(path)] == ["bug-example"]


def test_load_cases_names_the_line_of_a_bad_case(tmp_path: Path) -> None:
    good = EvalCase.model_validate(planted()).model_dump_json()
    path = tmp_path / "cases.jsonl"
    path.write_text(f'{good}\n{{"id": "broken"}}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 2"):
        load_cases(path)
