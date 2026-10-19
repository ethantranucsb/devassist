import pytest
from pydantic import ValidationError

from devassist.config import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    DEFAULT_REVIEW_MODEL,
    ESCALATION_ROUND,
    MAX_ROUNDS,
    MAX_SNIPPET_CHARS,
    REVIEW_ENABLED,
    LangSmithStatus,
    Settings,
    langsmith_status,
)
from devassist.exceptions import ConfigError, DevassistError, MissingAPIKeyError

LIMIT_VARS = ("DEVASSIST_MAX_ROUNDS", "DEVASSIST_ESCALATION_ROUND", "DEVASSIST_MAX_SNIPPET_CHARS")
REVIEW_VARS = ("DEVASSIST_REVIEW", "DEVASSIST_REVIEW_MODEL")
ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "DEVASSIST_MODEL",
    "DEVASSIST_FALLBACK_MODEL",
    *LIMIT_VARS,
    *REVIEW_VARS,
)
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


def test_the_review_defaults_to_the_config_constants() -> None:
    settings = load()

    assert settings.review is REVIEW_ENABLED
    assert settings.review_model == DEFAULT_REVIEW_MODEL


def test_the_review_is_on_by_default() -> None:
    # D21: every run gets the second review unless DEVASSIST_REVIEW or --no-review turns it off.
    assert load().review is True


def test_the_environment_turns_off_the_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVASSIST_REVIEW", "false")

    assert load().review is False


def test_a_different_model_reviews_the_drafting_models_work() -> None:
    assert DEFAULT_REVIEW_MODEL != DEFAULT_MODEL


def test_environment_turns_on_the_review_and_picks_its_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVASSIST_REVIEW", "true")
    monkeypatch.setenv("DEVASSIST_REVIEW_MODEL", "claude-test-reviewer")

    settings = load()

    assert settings.review is True
    assert settings.review_model == "claude-test-reviewer"


def test_loop_limits_default_to_the_config_constants() -> None:
    settings = load()

    assert settings.max_rounds == MAX_ROUNDS
    assert settings.escalation_round == ESCALATION_ROUND
    assert settings.max_snippet_chars == MAX_SNIPPET_CHARS


def test_environment_overrides_the_loop_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVASSIST_MAX_ROUNDS", "5")
    monkeypatch.setenv("DEVASSIST_ESCALATION_ROUND", "2")
    monkeypatch.setenv("DEVASSIST_MAX_SNIPPET_CHARS", "1000")

    settings = load()

    assert settings.max_rounds == 5
    assert settings.escalation_round == 2
    assert settings.max_snippet_chars == 1000


@pytest.mark.parametrize("name", LIMIT_VARS)
def test_loop_limits_below_one_are_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError, match=name):
        load()


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


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, LangSmithStatus.OFF),
        ({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "k"}, LangSmithStatus.ON),
        ({"LANGCHAIN_TRACING_V2": "true", "LANGCHAIN_API_KEY": "k"}, LangSmithStatus.ON),
        ({"LANGSMITH_TRACING": "true"}, LangSmithStatus.NO_KEY),
        ({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "  "}, LangSmithStatus.NO_KEY),
        ({"LANGSMITH_TRACING": "false", "LANGSMITH_API_KEY": "k"}, LangSmithStatus.OFF),
        ({"LANGSMITH_TRACING": "True", "LANGSMITH_API_KEY": "k"}, LangSmithStatus.OFF),
        (
            {
                "LANGSMITH_TRACING_V2": "false",
                "LANGSMITH_TRACING": "true",
                "LANGSMITH_API_KEY": "k",
            },
            LangSmithStatus.OFF,
        ),
    ],
    ids=[
        "nothing-set",
        "on",
        "older-names",
        "no-key",
        "blank-key",
        "off",
        "only-lowercase-true-counts",
        "v2-takes-priority",
    ],
)
def test_langsmith_status_follows_langsmiths_own_rules(
    environ: dict[str, str], expected: LangSmithStatus
) -> None:
    assert langsmith_status(environ) is expected


def test_missing_key_error_is_caught_as_a_devassist_error() -> None:
    assert issubclass(MissingAPIKeyError, ConfigError)
    assert issubclass(ConfigError, DevassistError)
