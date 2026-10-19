"""Pydantic schemas for what devassist returns, and the graph's state.

The model's structured output must match ``Analysis``. Class docstrings and
field descriptions end up in the JSON schema sent to the API, so they double
as instructions to the model. Rules the schema can't express simply, such as
line ranges and blank text, are checked by validators after the response
arrives, which keeps the schema itself flat.
"""

import operator
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from devassist.config import SUPPORTED_LANGUAGE


def _require_text(value: str) -> str:
    """Reject strings that are empty or contain only whitespace."""
    if not value.strip():
        raise ValueError("must not be empty")
    return value


NonBlankStr = Annotated[str, AfterValidator(_require_text)]


class Severity(StrEnum):
    """How serious an issue is."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Category(StrEnum):
    """What kind of issue it is."""

    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    DOCUMENTATION = "documentation"


class Issue(BaseModel):
    """One problem found in the snippet."""

    severity: Severity
    category: Category
    line_start: int = Field(description="First line of the problem, counting from 1.")
    line_end: int = Field(
        description="Last line of the problem; the same as line_start for a one-line problem."
    )
    description: NonBlankStr = Field(description="What is wrong and why it matters.")

    @field_validator("severity", "category", mode="before")
    @classmethod
    def _normalize_case(cls, value: object) -> object:
        """Accept any capitalization and surrounding spaces, such as "High" or " SECURITY "."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @model_validator(mode="after")
    def _check_line_range(self) -> Self:
        """Require lines counted from 1, with the end no earlier than the start."""
        if self.line_start < 1:
            raise ValueError("line_start must be 1 or more")
        if self.line_end < self.line_start:
            raise ValueError("line_end must not come before line_start")
        return self


class Analysis(BaseModel):
    """devassist's complete answer for one snippet."""

    summary: NonBlankStr = Field(description="What the code does, in one to three sentences.")
    documentation: NonBlankStr = Field(
        description="Markdown documentation for the snippet's functions and classes."
    )
    issues: list[Issue] = Field(description="Problems found; an empty list if there are none.")
    improved_code: NonBlankStr = Field(description="The complete improved version of the snippet.")


class Review(BaseModel):
    """A second reviewer's answer: the real problems the first review missed."""

    missed_issues: list[Issue] = Field(
        description=(
            "Real problems in the snippet that the reported issues don't cover; "
            "an empty list if the first review found them all."
        )
    )


class Finding(BaseModel):
    """One problem a static analyzer reported.

    Frozen, so findings can be compared and collected in sets when the verify
    step checks whether a rewrite added new problems.
    """

    model_config = ConfigDict(frozen=True)

    tool: str
    code: str
    message: str
    line: int


class Attempt(BaseModel):
    """One round's model answer, with the usage needed for cost metrics.

    Frozen, because a round's answer never changes once it arrives.
    ``escalated`` is recorded when the model is chosen, because the model
    that answers may report a dated ID that no longer matches the setting.
    ``retried`` says the client needed its validation retry for this round.
    """

    model_config = ConfigDict(frozen=True)

    round: int = Field(ge=1)
    model: str
    analysis: Analysis
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    escalated: bool = False
    retried: bool = False


class ReviewCall(BaseModel):
    """The second review's answer, with the usage needed for cost metrics.

    Frozen, like ``Attempt``. A review isn't a round: it returns missed
    issues, not an analysis, and fixing them takes a round of its own.
    """

    model_config = ConfigDict(frozen=True)

    model: str
    review: Review
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    retried: bool = False


class RunMetrics(BaseModel):
    """What one run used: rounds, reviews, models, tokens, money and time.

    Attributes:
        rounds: How many model answers the run took.
        models: The model that answered each round, in order.
        escalations: How many rounds used the fallback model.
        retries: How many model answers, rounds or review, needed the client's
            validation retry.
        reviews: How many second reviews ran: 0 or 1. Defaults to 0, so
            metrics saved before reviews existed still load.
        input_tokens: Input tokens across every round and the review.
        output_tokens: Output tokens across every round and the review.
        cost_usd: The total at list prices, or ``None`` if any model has no price.
        unpriced_models: The models that made the cost unknown.
        latency_seconds: Wall-clock time for the whole run.
    """

    model_config = ConfigDict(frozen=True)

    rounds: int = Field(ge=0)
    models: list[str]
    escalations: int = Field(ge=0)
    retries: int = Field(ge=0)
    reviews: int = Field(default=0, ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float | None
    unpriced_models: list[str]
    latency_seconds: float = Field(ge=0)


class GraphState(BaseModel):
    """Everything the graph knows about one run. Each node returns only the fields it changes.

    Attributes:
        snippet: The submitted code. A plain ``str`` rather than
            ``NonBlankStr``, so ``validate_input`` can report a blank snippet
            as an error instead of the state refusing to be created.
        language: The snippet's language; only Python is supported.
        error: Why the input was rejected, or ``None`` if it was accepted.
        findings: What the analyzers reported for the submitted snippet.
        attempts: One entry per round. LangGraph adds a node's new attempts to
            the end of this list instead of replacing it.
        problems: What the latest verify found wrong; empty if the latest
            answer passed.
        review: The second review, once it has run; at most one per run.
        result: The answer shown to the user, set by ``finalize``.
        warnings: Problems the final answer still has after the last round.
        metrics: What the run used. Set by ``run_graph`` once the graph has
            finished, since only it can time the whole run; no node writes it.
    """

    snippet: str
    language: str = SUPPORTED_LANGUAGE
    error: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    attempts: Annotated[list[Attempt], operator.add] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    review: ReviewCall | None = None
    result: Analysis | None = None
    warnings: list[str] = Field(default_factory=list)
    metrics: RunMetrics | None = None
