"""A scripted stand-in for a real model client, used by tests."""

from collections.abc import Iterable

from devassist.llm.base import LLMClient, LLMRequest, LLMResult
from devassist.models import Analysis

type ScriptStep = Analysis | LLMResult | Exception

FAKE_INPUT_TOKENS = 100
FAKE_OUTPUT_TOKENS = 50


class FakeLLMClient(LLMClient):
    """Returns scripted answers in order and records every request it receives.

    Each step in the script is an ``Analysis`` (returned with the requested
    model and placeholder token counts), a full ``LLMResult`` (returned as it
    is) or an exception (raised). Calling the fake more often than the script
    allows fails the test with an ``AssertionError``.

    Attributes:
        requests: Every request received, in order, including ones that raised.
    """

    def __init__(self, script: Iterable[ScriptStep]) -> None:
        """Create a fake that answers with the given steps, one per call.

        Args:
            script: The answers and errors to produce, in order.
        """
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    @property
    def remaining(self) -> int:
        """How many scripted steps haven't been used yet."""
        return len(self._script)

    def generate(self, request: LLMRequest) -> LLMResult:
        """Record the request, then return or raise the next scripted step.

        Args:
            request: The request a real client would send to the model.

        Returns:
            The next scripted answer.

        Raises:
            AssertionError: If no scripted steps are left.
            Exception: Any exception placed in the script, when its turn comes.
        """
        self.requests.append(request)
        if not self._script:
            raise AssertionError(
                f"FakeLLMClient was called {len(self.requests)} times, "
                f"but only {len(self.requests) - 1} steps were scripted"
            )
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, LLMResult):
            return step
        return LLMResult(
            analysis=step,
            model=request.model,
            input_tokens=FAKE_INPUT_TOKENS,
            output_tokens=FAKE_OUTPUT_TOKENS,
        )
