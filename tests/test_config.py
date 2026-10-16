import pytest

from devassist.config import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL, Settings
from devassist.exceptions import ConfigError, DevassistError, MissingAPIKeyError

ENV_VARS = ("ANTHROPIC_API_KEY", "DEVASSIST_MODEL", "DEVASSIST_FALLBACK_MODEL")
FAKE_KEY = "test-key-not-real"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove devassist's variables so the developer's own shell can't change results."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load() -> Settings:
    """Build settings from the environment only, never from a real .env file."""
    return Settings(_env_file=None)


def test_defaults_when_nothing_is_set() -> None:
    settings = load()

    assert settings.model == DEFAULT_MODEL
    assert settings.fallback_model == DEFAULT_FALLBACK_MODEL
    assert settings.anthropic_api_key is None


def test_environment_overrides_the_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVASSIST_MODEL", "claude-test-model")
    monkeypatch.setenv("DEVASSIST_FALLBACK_MODEL", "claude-test-fallback")

    settings = load()

    assert settings.model == "claude-test-model"
    assert settings.fallback_model == "claude-test-fallback"


def test_require_api_key_returns_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    assert load().require_api_key() == FAKE_KEY


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)

    with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
        load().require_api_key()


def test_api_key_is_hidden_when_settings_are_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)

    settings = load()

    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_missing_key_error_is_caught_as_a_devassist_error() -> None:
    assert issubclass(MissingAPIKeyError, ConfigError)
    assert issubclass(ConfigError, DevassistError)
