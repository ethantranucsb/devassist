"""Settings read from the environment and an optional .env file."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from devassist.exceptions import MissingAPIKeyError

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_FALLBACK_MODEL = "claude-sonnet-5-5"

# The draft, verify and refine loop
MAX_ROUNDS = 3
ESCALATION_ROUND = 3  # the first round that uses the fallback model
MAX_SNIPPET_CHARS = 20_000  # about 500 lines of code
SUPPORTED_LANGUAGE = "python"

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
