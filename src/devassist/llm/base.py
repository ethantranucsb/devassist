"""The interface every model client implements.

The graph depends only on ``LLMClient``, so tests can pass ``FakeLLMClient``
and the real Anthropic client can be swapped in without changing the graph.
Prompts are built outside the client; a client only turns a request into an
API call and the response into an ``LLMResult``.
"""

from dataclasses import dataclass
from typing import Protocol

from devassist.models import Analysis


@dataclass(frozen=True)
class LLMRequest:
    """One model call: which model to use and what to send it.

    Attributes:
        model: The model ID, such as ``claude-haiku-5-5``.
        system: The system prompt.
        prompt: The user prompt, with the snippet inside its delimiters.
    """

    model: str
    system: str
    prompt: str


@dataclass(frozen=True)
class LLMResult:
    """A validated answer from the model, with the usage needed for cost metrics.

    Attributes:
        analysis: The model's answer, already validated against the schema.
        model: The model that actually answered.
        input_tokens: Input tokens billed for the call, including any retry.
        output_tokens: Output tokens billed for the call, including any retry and any
            thinking tokens.
        retried: Whether the first answer failed validation and a retry was needed.
    """

    analysis: Analysis
    model: str
    input_tokens: int
    output_tokens: int
    retried: bool = False


class LLMClient(Protocol):
    """Anything that can turn an ``LLMRequest`` into a validated ``LLMResult``."""

    def generate(self, request: LLMRequest) -> LLMResult:
        """Call the model and return its validated answer.

        Args:
            request: The model to use and the prompts to send.

        Returns:
            The validated analysis, the model that answered and the token usage.

        Raises:
            LLMError: If the call fails or the answer can't be used. An answer
                that still doesn't match the schema after one retry raises
                ``SchemaValidationError``.
        """
