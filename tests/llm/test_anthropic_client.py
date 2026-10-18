import json
from typing import Any

import httpx2
import pytest

from devassist.config import LLM_MAX_RETRIES, LLM_TIMEOUT_SECONDS, MAX_OUTPUT_TOKENS
from devassist.exceptions import LLMError, SchemaValidationError
from devassist.llm import LLMRequest
from devassist.llm.anthropic_client import AnthropicClient
from devassist.models import Analysis

FAKE_KEY = "sk-ant-test-not-real"
REQUEST = LLMRequest(model="claude-haiku-5-5", system="You review code.", prompt="Review this.")
VALID = {
    "summary": "Adds two numbers.",
    "documentation": "`add(a, b)` returns a + b.",
    "issues": [],
    "improved_code": "def add(a, b):\n    return a + b\n",
}
# Valid JSON that matches the schema's shape but fails a validator: the summary is blank.
INVALID = {**VALID, "summary": "   "}


def ok(
    text: str,
    stop_reason: str = "end_turn",
    model: str = "claude-haiku-5-5",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> httpx2.Response:
    """Return a successful Messages API response whose answer is the given text."""
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    )


def api_error(status: int, kind: str, headers: dict[str, str] | None = None) -> httpx2.Response:
    """Return an error response shaped like the Messages API's."""
    body = {"type": "error", "error": {"type": kind, "message": f"{kind} from the test"}}
    return httpx2.Response(status, json=body, headers=headers)


class FakeAPI:
    """Answers each request with the next scripted response and records every request body."""

    def __init__(self, *steps: httpx2.Response | Exception) -> None:
        self._steps = list(steps)
        self.bodies: list[dict[str, Any]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_client(api: FakeAPI, max_retries: int = 0) -> AnthropicClient:
    """Return a client whose HTTP traffic goes to the fake API instead of the network."""
    transport = httpx2.MockTransport(api.handle)
    return AnthropicClient(
        FAKE_KEY, max_retries=max_retries, http_client=httpx2.Client(transport=transport)
    )


def test_the_request_carries_the_model_prompts_limit_and_schema() -> None:
    api = FakeAPI(ok(json.dumps(VALID)))

    make_client(api).generate(REQUEST)

    body = api.bodies[0]
    assert body["model"] == "claude-haiku-5-5"
    assert body["system"] == "You review code."
    assert body["messages"] == [{"role": "user", "content": "Review this."}]
    assert body["max_tokens"] == MAX_OUTPUT_TOKENS
    output_format = body["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert set(output_format["schema"]["properties"]) == set(Analysis.model_fields)


def test_the_result_has_the_analysis_the_answering_model_and_the_usage() -> None:
    api = FakeAPI(ok(json.dumps(VALID), model="claude-haiku-5-5-20261001", input_tokens=11))

    result = make_client(api).generate(REQUEST)

    assert result.analysis == Analysis.model_validate(VALID)
    assert result.model == "claude-haiku-5-5-20261001"
    assert result.input_tokens == 11
    assert result.output_tokens == 20
    assert result.retried is False


def test_a_refusal_is_an_error_and_is_not_retried() -> None:
    api = FakeAPI(ok("I can't help with that.", stop_reason="refusal"))

    with pytest.raises(LLMError, match="declined"):
        make_client(api).generate(REQUEST)

    assert len(api.bodies) == 1


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
def test_a_cut_off_answer_is_an_error_and_is_not_retried(stop_reason: str) -> None:
    api = FakeAPI(ok(json.dumps(VALID)[:40], stop_reason=stop_reason))

    with pytest.raises(LLMError, match=stop_reason):
        make_client(api).generate(REQUEST)

    assert len(api.bodies) == 1


def test_an_invalid_answer_is_retried_once_with_the_errors_fed_back() -> None:
    bad = json.dumps(INVALID)
    api = FakeAPI(
        ok(bad, input_tokens=10, output_tokens=20),
        ok(json.dumps(VALID), input_tokens=30, output_tokens=40),
    )

    result = make_client(api).generate(REQUEST)

    assert result.analysis == Analysis.model_validate(VALID)
    assert result.retried is True
    retry = api.bodies[1]["messages"]
    assert retry[0] == {"role": "user", "content": "Review this."}
    assert retry[1] == {"role": "assistant", "content": bad}
    assert "- summary: Value error, must not be empty" in retry[2]["content"]


def test_both_calls_count_toward_the_usage() -> None:
    api = FakeAPI(
        ok(json.dumps(INVALID), input_tokens=10, output_tokens=20),
        ok(json.dumps(VALID), input_tokens=30, output_tokens=40),
    )

    result = make_client(api).generate(REQUEST)

    assert result.input_tokens == 40
    assert result.output_tokens == 60


def test_an_answer_that_is_not_json_is_retried() -> None:
    api = FakeAPI(ok("Sure! Here is my review."), ok(json.dumps(VALID)))

    make_client(api).generate(REQUEST)

    assert "- answer: Invalid JSON" in api.bodies[1]["messages"][2]["content"]


def test_a_second_invalid_answer_raises_schema_validation_error() -> None:
    api = FakeAPI(ok(json.dumps(INVALID)), ok(json.dumps(INVALID)))

    with pytest.raises(SchemaValidationError, match="summary"):
        make_client(api).generate(REQUEST)

    assert len(api.bodies) == 2


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (httpx2.ReadTimeout("timed out"), "timed out"),
        (httpx2.ConnectError("no route to host"), "Couldn't reach"),
        (api_error(401, "authentication_error"), "ANTHROPIC_API_KEY"),
        (api_error(429, "rate_limit_error"), "rate limit"),
        (api_error(500, "api_error"), "500"),
    ],
    ids=["timeout", "connection", "bad-key", "rate-limit", "server-error"],
)
def test_api_failures_become_llm_errors(step: httpx2.Response | Exception, message: str) -> None:
    api = FakeAPI(step)

    with pytest.raises(LLMError, match=message) as caught:
        make_client(api).generate(REQUEST)

    assert FAKE_KEY not in str(caught.value)


def test_the_sdk_retries_a_server_error_when_retries_are_allowed() -> None:
    # retry-after-ms tells the SDK to wait 1 ms instead of its usual backoff.
    api = FakeAPI(api_error(500, "api_error", {"retry-after-ms": "1"}), ok(json.dumps(VALID)))

    make_client(api, max_retries=1).generate(REQUEST)

    assert len(api.bodies) == 2


def test_the_sdk_client_gets_an_explicit_timeout_and_retry_limit() -> None:
    client = AnthropicClient(FAKE_KEY)

    # A private attribute, read here only to prove the limits from config.py are applied.
    assert client._client.timeout == LLM_TIMEOUT_SECONDS
    assert client._client.max_retries == LLM_MAX_RETRIES
