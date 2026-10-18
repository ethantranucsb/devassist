"""The eval's labeled cases: what each one contains, and how to load them.

Each line of ``cases.jsonl`` is one case: a snippet plus its answer key. The
schema enforces the rules that keep the answer key honest, such as a planted
case always naming its issue and every line range fitting inside its snippet.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from devassist.config import SUPPORTED_LANGUAGE
from devassist.models import Category

CASES_PATH = Path(__file__).with_name("cases.jsonl")


class Kind(StrEnum):
    """What a case tests."""

    PLANTED = "planted"  # issues put there on purpose; scored on recall
    CLEAN = "clean"  # good code; any reported issue is a false alarm
    ADVERSARIAL = "adversarial"  # hostile or invalid input; scored pass or fail


class Difficulty(StrEnum):
    """How hard a planted issue is to spot, so recall can be reported by difficulty."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Behavior(StrEnum):
    """What a run must do with the case."""

    REVIEW = "review"  # a normal review, scored on the issues it reports
    INPUT_ERROR = "input_error"  # rejected before any model call
    REWRITE_PARSES = "rewrite_parses"  # broken code whose improved version must parse


class ExpectedIssue(BaseModel):
    """One issue the answer key says a good review reports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: Category
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    note: str = Field(min_length=1, description="What the issue is, for human readers.")

    @model_validator(mode="after")
    def _check_line_range(self) -> Self:
        """Require the range to end no earlier than it starts."""
        if self.line_end < self.line_start:
            raise ValueError("line_end must not come before line_start")
        return self


class EvalCase(BaseModel):
    """A snippet and its answer key.

    Attributes:
        id: A unique name, lowercase words joined by hyphens.
        kind: Planted, clean or adversarial.
        difficulty: How hard the planted issues are to spot; planted cases only.
        behavior: What a run must do; anything but a review is adversarial only.
        language: The language the snippet is submitted as.
        snippet: The code, or the piece of it that ``repeat`` copies.
        repeat: How many copies of ``snippet`` make up the code, so an
            oversized input doesn't need a 20,000-character line in the file.
        expected_issues: The issues a good review reports.
        smoke: Whether the case is in the cheap 12-case first run.
        note: Why the case exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    kind: Kind
    difficulty: Difficulty | None = None
    behavior: Behavior = Behavior.REVIEW
    language: str = SUPPORTED_LANGUAGE
    snippet: str
    repeat: int = Field(default=1, ge=1)
    expected_issues: list[ExpectedIssue] = Field(default_factory=list)
    smoke: bool = False
    note: str = ""

    @property
    def code(self) -> str:
        """The code a run submits: the snippet, repeated ``repeat`` times."""
        return self.snippet * self.repeat

    @model_validator(mode="after")
    def _check_kind_rules(self) -> Self:
        """Enforce what each kind of case must and mustn't contain."""
        if self.kind is Kind.PLANTED:
            if not self.expected_issues:
                raise ValueError("a planted case needs at least one expected issue")
            if self.difficulty is None:
                raise ValueError("a planted case needs a difficulty")
        if self.kind is Kind.CLEAN and self.expected_issues:
            raise ValueError("a clean case can't expect issues")
        if self.kind is not Kind.ADVERSARIAL and self.behavior is not Behavior.REVIEW:
            raise ValueError("only adversarial cases may expect a behavior other than review")
        line_count = len(self.code.splitlines())
        for issue in self.expected_issues:
            if issue.line_end > line_count:
                raise ValueError(
                    f"an expected issue ends on line {issue.line_end}, "
                    f"but the code has {line_count} lines"
                )
        return self


def load_cases(path: Path = CASES_PATH) -> list[EvalCase]:
    """Read the cases, one JSON object per line, skipping blank lines.

    Args:
        path: The JSONL file to read.

    Returns:
        The cases, in file order.

    Raises:
        ValueError: If a line isn't a valid case. The message names the line.
    """
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(EvalCase.model_validate_json(line))
        except ValidationError as error:
            raise ValueError(f"{path.name} line {number} isn't a valid case: {error}") from error
    return cases
