import pytest

from devassist.config import Settings
from devassist.exceptions import MissingAPIKeyError
from devassist.llm import LLMRequest
from devassist.llm.anthropic_client import AnthropicClient
from devassist.models import Analysis
from devassist.prompts import draft_prompt, system_prompt

pytestmark = pytest.mark.live

SNIPPET = "def add(a, b):\n    return a+b\n"


def test_the_default_model_returns_a_valid_analysis() -> None:
    settings = Settings()
    try:
        api_key = settings.require_api_key()
    except MissingAPIKeyError:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    request = LLMRequest(
        model=settings.model,
        system=system_prompt(),
        prompt=draft_prompt(SNIPPET, []),
        output_type=Analysis,
    )

    result = AnthropicClient(api_key).generate(request)

    assert "def add" in result.output.improved_code
    assert result.input_tokens > 0
    assert result.output_tokens > 0
