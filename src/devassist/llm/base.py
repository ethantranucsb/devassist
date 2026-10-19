"""The interface every model client implements.

The graph depends only on ``LLMClient``, so tests can pass ``FakeLLMClient``
and the real Anthropic client can be swapped in without changing the graph.
Prompts are built outside the client; a client only turns a request into an
API call and the response into an ``LLMResult``. Each request names the
Pydantic model its answer must match, so one client serves every node that
calls a model.
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class LLMRequest[T: BaseModel]:
    """One model call: which model to use, what to send it and what shape the answer takes.

    Attributes:
        model: The model ID, such as ``claude-haiku-5-5``.
        system: The system prompt.
        prompt: The user prompt, with the snippet inside its delimiters.
        output_type: The Pydantic model the answer must match, such as ``Analysis``.
    """

    model: str
    system: str
    prompt: str
    output_type: type[T]


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    """A validated answer from the model, with the usage needed for cost metrics.

    Attributes:
        output: The model's answer, already validated against the request's output type.
        model: The model that actually answered.
        input_tokens: Input tokens billed for the call, including any retry.
        output_tokens: Output tokens billed for the call, including any retry and any
            thinking tokens.
        retried: Whether the first answer failed validation and a retry was needed.
    """

    output: T
    model: str
    input_tokens: int
    output_tokens: int
    retried: bool = False


class LLMClient(Protocol):
    """Anything that can turn an ``LLMRequest`` into a validated ``LLMResult``."""

    def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        """Call the model and return its validated answer.

        Args:
            request: The model to use, the prompts to send and the answer's schema.

        Returns:
            The validated answer, the model that answered and the token usage.

        Raises:
            LLMError: If the call fails or the answer can't be used. An answer
                that still doesn't match the schema after one retry raises
                ``SchemaValidationError``.
        """
