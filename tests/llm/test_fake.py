import pytest

from devassist.exceptions import DevassistError, LLMError, SchemaValidationError
from devassist.llm import FakeLLMClient, LLMRequest, LLMResult
from devassist.models import Analysis


def make_analysis(summary: str = "Adds two numbers.") -> Analysis:
    """Return a valid analysis with the given summary."""
    return Analysis(
        summary=summary,
        documentation="`add(a, b)` returns the sum of a and b.",
        issues=[],
        improved_code="def add(a: int, b: int) -> int:\n    return a + b\n",
    )


def make_request(model: str = "claude-test-model") -> LLMRequest:
    """Return a request for the given model."""
    return LLMRequest(
        model=model, system="You review Python code.", prompt="<snippet>...</snippet>"
    )


def test_returns_scripted_analyses_in_order() -> None:
    first, second = make_analysis("First."), make_analysis("Second.")
    fake = FakeLLMClient([first, second])

    assert fake.generate(make_request()).analysis == first
    assert fake.generate(make_request()).analysis == second


def test_reports_the_requested_model_and_token_counts() -> None:
    fake = FakeLLMClient([make_analysis()])

    result = fake.generate(make_request(model="claude-haiku-test"))

    assert result.model == "claude-haiku-test"
    assert result.input_tokens > 0
    assert result.output_tokens > 0


def test_returns_a_scripted_result_unchanged() -> None:
    scripted = LLMResult(
        analysis=make_analysis(), model="claude-other", input_tokens=1234, output_tokens=567
    )
    fake = FakeLLMClient([scripted])

    assert fake.generate(make_request()) is scripted


def test_records_every_request() -> None:
    fake = FakeLLMClient([make_analysis(), make_analysis()])
    first = make_request(model="claude-haiku-test")
    second = make_request(model="claude-sonnet-test")

    fake.generate(first)
    fake.generate(second)

    assert fake.requests == [first, second]


def test_raises_a_scripted_error_and_still_records_the_request() -> None:
    fake = FakeLLMClient([SchemaValidationError("summary: must not be empty")])

    with pytest.raises(SchemaValidationError, match="summary"):
        fake.generate(make_request())

    assert len(fake.requests) == 1


def test_fails_loudly_when_called_more_than_scripted() -> None:
    fake = FakeLLMClient([make_analysis()])
    fake.generate(make_request())

    with pytest.raises(AssertionError, match="scripted"):
        fake.generate(make_request())


def test_remaining_counts_unused_steps() -> None:
    fake = FakeLLMClient([make_analysis(), make_analysis()])
    assert fake.remaining == 2

    fake.generate(make_request())

    assert fake.remaining == 1


def test_schema_validation_error_is_caught_as_a_devassist_error() -> None:
    assert issubclass(SchemaValidationError, LLMError)
    assert issubclass(LLMError, DevassistError)
