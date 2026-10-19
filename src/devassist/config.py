"""Settings read from the environment and an optional .env file."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from devassist.exceptions import MissingAPIKeyError

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_FALLBACK_MODEL = "claude-sonnet-5-5"
# A different model checks the drafting model's review for missed issues (docs/decisions.md D18).
DEFAULT_REVIEW_MODEL = DEFAULT_FALLBACK_MODEL


@dataclass(frozen=True)
class Price:
    """A model's list price, in US dollars per million tokens."""

    input_per_million: float
    output_per_million: float


# List prices checked Oct 13, 2026 (docs/decisions.md D8), from
# https://platform.claude.com/docs/en/about-claude/pricing. A model missing
# here gets an unknown cost rather than a wrong one.
PRICES: dict[str, Price] = {
    DEFAULT_MODEL: Price(input_per_million=0.10, output_per_million=0.50),
    DEFAULT_FALLBACK_MODEL: Price(input_per_million=2.00, output_per_million=10.00),
}

# The draft, verify and refine loop
MAX_ROUNDS = 3
ESCALATION_ROUND = 3  # the first round that uses the fallback model
MAX_SNIPPET_CHARS = 20_000  # about 500 lines of code
SUPPORTED_LANGUAGE = "python"
REVIEW_ENABLED = True  # on by default despite missing D8's bar (docs/decisions.md D21)
SAVED_REVIEW_SUFFIX = ".review.md"  # find_users.py's review is saved as find_users.review.md

# Anthropic API
LLM_TIMEOUT_SECONDS = 120.0  # per request; the SDK's default is 10 minutes
LLM_MAX_RETRIES = 2  # SDK retries for rate limits, server errors and dropped connections
MAX_OUTPUT_TOKENS = 16_000  # docs plus a rewrite of the largest snippet allowed

# Static analyzers
ANALYZER_TIMEOUT_SECONDS = 30.0
RUFF_TARGET_VERSION = "py313"
RUFF_SELECT = ("E", "F", "B")  # pycodestyle errors, pyflakes, bugbear
RUFF_IGNORE = ("E501",)  # line length is style, not a problem worth a fix


class Settings(BaseSettings):
    """devassist's settings, loaded from environment variables and `.env`.

    Environment variables take priority over `.env`, and both take priority
    over the defaults below.

    Attributes:
        anthropic_api_key: The Anthropic API key, from ``ANTHROPIC_API_KEY``.
        model: The model for rounds before the escalation round, from ``DEVASSIST_MODEL``.
        fallback_model: The model from the escalation round on, from
            ``DEVASSIST_FALLBACK_MODEL``.
        max_rounds: The most model answers per run, from ``DEVASSIST_MAX_ROUNDS``.
        escalation_round: The first round that uses the fallback model, from
            ``DEVASSIST_ESCALATION_ROUND``.
        max_snippet_chars: The longest snippet accepted, in characters, from
            ``DEVASSIST_MAX_SNIPPET_CHARS``.
        review: Whether a second model checks a passing answer for missed issues,
            from ``DEVASSIST_REVIEW``.
        review_model: The model for that second review, from ``DEVASSIST_REVIEW_MODEL``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = Field(default=DEFAULT_MODEL, validation_alias="DEVASSIST_MODEL")
    fallback_model: str = Field(
        default=DEFAULT_FALLBACK_MODEL, validation_alias="DEVASSIST_FALLBACK_MODEL"
    )
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, validation_alias="DEVASSIST_MAX_ROUNDS")
    escalation_round: int = Field(
        default=ESCALATION_ROUND, ge=1, validation_alias="DEVASSIST_ESCALATION_ROUND"
    )
    max_snippet_chars: int = Field(
        default=MAX_SNIPPET_CHARS, ge=1, validation_alias="DEVASSIST_MAX_SNIPPET_CHARS"
    )
    review: bool = Field(default=REVIEW_ENABLED, validation_alias="DEVASSIST_REVIEW")
    review_model: str = Field(
        default=DEFAULT_REVIEW_MODEL, validation_alias="DEVASSIST_REVIEW_MODEL"
    )

    def require_api_key(self) -> str:
        """Return the API key, or raise if none is configured.

        Only code that calls a model should ask for the key, so commands
        such as ``--version`` work without one.

        Returns:
            The API key as plain text, for handing to the SDK client.

        Raises:
            MissingAPIKeyError: If ``ANTHROPIC_API_KEY`` is unset or empty.
        """
        if self.anthropic_api_key is None or not self.anthropic_api_key.get_secret_value():
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key, "
                "or set the variable in your shell."
            )
        return self.anthropic_api_key.get_secret_value()


class LangSmithStatus(StrEnum):
    """Whether LangGraph will send traces to LangSmith."""

    OFF = "off"
    ON = "on"
    NO_KEY = "no-key"


def _langsmith_variable(environ: Mapping[str, str], name: str) -> str | None:
    """Read a LangSmith variable under either prefix, treating blank values as unset."""
    for prefix in ("LANGSMITH", "LANGCHAIN"):
        value = environ.get(f"{prefix}_{name}")
        if value is not None and value.strip():
            return value
    return None


def langsmith_status(environ: Mapping[str, str]) -> LangSmithStatus:
    """Decide whether tracing is on, by the same rules the LangSmith library uses.

    LangSmith reads its variables from the process environment, not from
    devassist's ``.env`` (docs/decisions.md D15). ``TRACING_V2`` takes
    priority over ``TRACING``, and only the exact value ``true`` turns it on.

    Args:
        environ: The environment variables, usually ``os.environ``.

    Returns:
        ``ON`` if traces will be sent, ``NO_KEY`` if tracing is requested
        without an API key, and ``OFF`` otherwise.
    """
    tracing = _langsmith_variable(environ, "TRACING_V2") or _langsmith_variable(environ, "TRACING")
    if tracing != "true":
        return LangSmithStatus.OFF
    if _langsmith_variable(environ, "API_KEY") is None:
        return LangSmithStatus.NO_KEY
    return LangSmithStatus.ON
