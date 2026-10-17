import os

import pytest

TRACING_PREFIXES = ("LANGSMITH_", "LANGCHAIN_")


@pytest.fixture(autouse=True)
def no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove LangSmith's variables, so no test can send a trace whatever the shell has set."""
    for name in list(os.environ):
        if name.upper().startswith(TRACING_PREFIXES):
            monkeypatch.delenv(name)
