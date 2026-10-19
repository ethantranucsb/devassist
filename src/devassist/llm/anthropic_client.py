"""The real model client: Claude, called through the Anthropic SDK.

The request carries the JSON schema of its output type, such as
``Analysis``, as the output format, so the API returns JSON in that shape.
The client then checks why the model stopped and validates the answer with
Pydantic itself. It calls ``messages.create()`` rather than
``messages.parse()``, because ``parse()`` validates inside the SDK: an
invalid answer raises before the stop reason can be checked, and the answer
the retry needs to send back is lost (D14).
"""

import logging
from collections.abc import Sequence
from typing import Any

import anthropic
import httpx2
from anthropic.types import Message, MessageParam, TextBlock
from pydantic import BaseModel, ValidationError

from devassist.config import LLM_MAX_RETRIES, LLM_TIMEOUT_SECONDS, MAX_OUTPUT_TOKENS
from devassist.exceptions import LLMError, SchemaValidationError
from devassist.llm.base import LLMClient, LLMRequest, LLMResult
from devassist.prompts import retry_prompt

logger = logging.getLogger(__name__)


def output_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    """Build the JSON schema the API holds an answer to.

    Args:
        output_type: The Pydantic model the answer must match.

    Returns:
        The schema in the form the API accepts.
    """
    return anthropic.transform_schema(output_type)


class AnthropicClient(LLMClient):
    """Sends requests to Claude and returns validated answers."""

    def __init__(
        self,
        api_key: str,
        *,
        max_tokens: int = MAX_OUTPUT_TOKENS,
        timeout: float = LLM_TIMEOUT_SECONDS,
        max_retries: int = LLM_MAX_RETRIES,
        http_client: httpx2.Client | None = None,
    ) -> None:
        """Create a client with an explicit timeout and retry limit.

        Args:
            api_key: The Anthropic API key.
            max_tokens: The most output tokens one answer may use.
            timeout: Seconds to wait for one request before giving up.
            max_retries: How many times the SDK retries rate limits, server
                errors and dropped connections, with backoff.
            http_client: The HTTP client to send requests with. Tests pass one
                with a fake transport; normally the SDK creates its own.
        """
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries, http_client=http_client
        )
        self._max_tokens = max_tokens
        self._timeout = timeout

    def generate[T: BaseModel](self, request: LLMRequest[T]) -> LLMResult[T]:
        """Call the model, retrying once with the errors if its answer fails validation.

        Args:
            request: The model to use, the prompts to send and the answer's schema.

        Returns:
            The validated answer, the model that answered and the tokens of
            every call made, including a retry.

        Raises:
            LLMError: If a call fails, or the model refuses or stops early.
            SchemaValidationError: If the answer still fails validation after the retry.
        """
        validate = request.output_type.model_validate_json
        messages: list[MessageParam] = [{"role": "user", "content": request.prompt}]
        first = self._send(request, messages)
        try:
            return _result(validate(_text(first)), [first])
        except ValidationError as error:
            logger.info("The answer from %s failed validation; retrying once", first.model)
            messages = [
                *messages,
                {"role": "assistant", "content": _text(first)},
                {"role": "user", "content": retry_prompt(_error_lines(error))},
            ]
        second = self._send(request, messages)
        try:
            return _result(validate(_text(second)), [first, second])
        except ValidationError as error:
            details = "\n".join(_error_lines(error))
            raise SchemaValidationError(
                f"The model's answer still failed validation after a retry:\n{details}"
            ) from error

    def _send(self, request: LLMRequest[Any], messages: list[MessageParam]) -> Message:
        """Make one API call and check that the model finished its answer."""
        schema = output_schema(request.output_type)
        try:
            response = self._client.messages.create(
                model=request.model,
                max_tokens=self._max_tokens,
                system=request.system,
                messages=messages,
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APITimeoutError as error:
            raise LLMError(f"The model call timed out after {self._timeout:g} seconds.") from error
        except anthropic.APIConnectionError as error:
            raise LLMError("Couldn't reach the Anthropic API. Check your connection.") from error
        except anthropic.AuthenticationError as error:
            raise LLMError(
                "The Anthropic API rejected the key. Check ANTHROPIC_API_KEY."
            ) from error
        except anthropic.RateLimitError as error:
            raise LLMError(
                "The Anthropic API's rate limit was reached. Try again later."
            ) from error
        except anthropic.APIStatusError as error:
            raise LLMError(
                f"The Anthropic API returned an error ({error.status_code}): {error.message}"
            ) from error
        if response.stop_reason == "refusal":
            raise LLMError("The model declined to answer.")
        if response.stop_reason != "end_turn":
            raise LLMError(
                f"The model stopped before finishing its answer ({response.stop_reason})."
            )
        return response


def _text(response: Message) -> str:
    """Join the text blocks of a response."""
    return "".join(block.text for block in response.content if isinstance(block, TextBlock))


def _error_lines(error: ValidationError) -> list[str]:
    """Describe each validation error on one line, such as ``summary: Value error, ...``.

    The rejected values are left out, so an error line never repeats the answer.
    """
    return [
        f"{'.'.join(str(part) for part in item['loc']) or 'answer'}: {item['msg']}"
        for item in error.errors()
    ]


def _result[T: BaseModel](output: T, responses: Sequence[Message]) -> LLMResult[T]:
    """Combine the validated answer with the usage of every call that produced it."""
    return LLMResult(
        output=output,
        model=responses[-1].model,
        input_tokens=sum(response.usage.input_tokens for response in responses),
        output_tokens=sum(response.usage.output_tokens for response in responses),
        retried=len(responses) > 1,
    )
