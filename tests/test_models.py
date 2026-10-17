import pytest
from pydantic import ValidationError

from devassist.models import Analysis, Attempt, Category, GraphState, Issue, Severity


def make_issue(**overrides: object) -> dict[str, object]:
    """Return valid issue data, with any fields replaced by the overrides."""
    data: dict[str, object] = {
        "severity": "high",
        "category": "security",
        "line_start": 3,
        "line_end": 4,
        "description": "Builds a SQL query with string formatting, which allows injection.",
    }
    data.update(overrides)
    return data


def make_analysis(**overrides: object) -> dict[str, object]:
    """Return valid analysis data, with any fields replaced by the overrides."""
    data: dict[str, object] = {
        "summary": "Looks up a user by name.",
        "documentation": "`find_user(name)` returns the matching row, or None.",
        "issues": [make_issue()],
        "improved_code": "def find_user(name: str) -> None:\n    ...\n",
    }
    data.update(overrides)
    return data


def test_valid_analysis_parses() -> None:
    analysis = Analysis.model_validate(make_analysis())

    assert analysis.summary == "Looks up a user by name."
    assert analysis.issues[0].severity is Severity.HIGH
    assert analysis.issues[0].category is Category.SECURITY


def test_empty_issue_list_is_valid() -> None:
    analysis = Analysis.model_validate(make_analysis(issues=[]))

    assert analysis.issues == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("High", Severity.HIGH), ("LOW", Severity.LOW), (" medium ", Severity.MEDIUM)],
)
def test_severity_accepts_any_capitalization(raw: str, expected: Severity) -> None:
    assert Issue.model_validate(make_issue(severity=raw)).severity is expected


def test_category_accepts_any_capitalization() -> None:
    issue = Issue.model_validate(make_issue(category="SECURITY"))

    assert issue.category is Category.SECURITY


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(ValidationError, match="category"):
        Issue.model_validate(make_issue(category="vibes"))


def test_non_text_severity_is_rejected() -> None:
    with pytest.raises(ValidationError, match="severity"):
        Issue.model_validate(make_issue(severity=3))


def test_line_numbers_start_at_one() -> None:
    with pytest.raises(ValidationError, match="line_start"):
        Issue.model_validate(make_issue(line_start=0, line_end=0))


def test_line_end_cannot_come_before_line_start() -> None:
    with pytest.raises(ValidationError, match="line_end"):
        Issue.model_validate(make_issue(line_start=5, line_end=2))


def test_single_line_issue_is_valid() -> None:
    issue = Issue.model_validate(make_issue(line_start=7, line_end=7))

    assert issue.line_start == issue.line_end == 7


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"], ids=["empty", "spaces", "newline-tab"])
def test_blank_description_is_rejected(blank: str) -> None:
    with pytest.raises(ValidationError, match="description"):
        Issue.model_validate(make_issue(description=blank))


@pytest.mark.parametrize("field", ["summary", "documentation", "improved_code"])
def test_blank_text_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        Analysis.model_validate(make_analysis(**{field: "   "}))


def test_missing_field_is_rejected() -> None:
    data = make_analysis()
    del data["improved_code"]

    with pytest.raises(ValidationError, match="improved_code"):
        Analysis.model_validate(data)


def test_json_round_trip_keeps_everything() -> None:
    original = Analysis.model_validate(make_analysis())

    restored = Analysis.model_validate_json(original.model_dump_json())

    assert restored == original


def test_attempt_rounds_start_at_one() -> None:
    with pytest.raises(ValidationError, match="round"):
        Attempt(
            round=0,
            model="claude-haiku-5-5",
            analysis=Analysis.model_validate(make_analysis()),
            input_tokens=100,
            output_tokens=50,
        )


def test_graph_state_starts_with_only_the_snippet() -> None:
    state = GraphState(snippet="x = 1\n")

    assert state.language == "python"
    assert state.error is None
    assert state.findings == []
    assert state.attempts == []
    assert state.problems == []
    assert state.result is None
    assert state.warnings == []


def test_graph_state_accepts_a_blank_snippet_so_validation_can_report_it() -> None:
    assert GraphState(snippet="").snippet == ""
