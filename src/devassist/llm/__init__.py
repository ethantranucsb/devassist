"""Model clients: the protocol the graph depends on, and its implementations."""

from devassist.llm.base import LLMClient, LLMRequest, LLMResult
from devassist.llm.fake import FakeLLMClient

__all__ = ["FakeLLMClient", "LLMClient", "LLMRequest", "LLMResult"]
