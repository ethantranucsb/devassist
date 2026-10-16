"""Settings read from the environment and an optional .env file."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from devassist.exceptions import MissingAPIKeyError

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_FALLBACK_MODEL = "claude-sonnet-5-5"


class Settings(BaseSettings):
    """devassist's settings, loaded from environment variables and `.env`.

    Environment variables take priority over `.env`, and both take priority
    over the defaults below.

    Attributes:
        anthropic_api_key: The Anthropic API key, from ``ANTHROPIC_API_KEY``.
        model: The model for rounds 1 and 2, from ``DEVASSIST_MODEL``.
        fallback_model: The model for round 3, from ``DEVASSIST_FALLBACK_MODEL``.
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
