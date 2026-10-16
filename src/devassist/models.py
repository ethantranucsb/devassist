"""Pydantic schemas for what devassist returns.

The model's structured output must match ``Analysis``. Class docstrings and
field descriptions end up in the JSON schema sent to the API, so they double
as instructions to the model. Rules the schema can't express simply, such as
line ranges and blank text, are checked by validators after the response
arrives, which keeps the schema itself flat.
"""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator


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
