import json
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from devassist import cli
from devassist.analyzers import Analyzer
from devassist.cli import ExitCode, build_app
from devassist.exceptions import AnalyzerError, DevassistError, LLMError, SchemaValidationError
from devassist.llm import FakeLLMClient
from devassist.llm.fake import ScriptStep
from devassist.models import Analysis, Finding, Issue

ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "DEVASSIST_MODEL",
    "DEVASSIST_FALLBACK_MODEL",
    "DEVASSIST_MAX_ROUNDS",
    "DEVASSIST_ESCALATION_ROUND",
    "DEVASSIST_MAX_SNIPPET_CHARS",
)
SNIPPET = "def add(a, b):\n    return a+b\n"
GOOD_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
# Renames the parameters, so verify rejects it even with no analyzers.
BAD_CODE = "def add(x, y):\n    return x + y\n"
ISSUE = Issue(
    severity="high", category="bug", line_start=1, line_end=2, description="No type checks."
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run in an empty folder with devassist's variables cleared, so no real .env or key is used."""
    monkeypatch.chdir(tmp_path)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class FailingAnalyzer(Analyzer):
    """An analyzer that always fails, as a crashed ruff or bandit would."""

    name = "failing"

    def analyze(self, code: str) -> list[Finding]:
        raise AnalyzerError("ruff crashed.")


def answer(code: str = GOOD_CODE, issues: Sequence[Issue] = (), summary: str = "Adds.") -> Analysis:
    """Return a model answer with the given improved code, issues and summary."""
    return Analysis(
        summary=summary,
        documentation="`add(a, b)` returns the sum.",
        issues=list(issues),
        improved_code=code,
    )


def fake_app(
    *script: ScriptStep, analyzers: Sequence[Analyzer] = ()
) -> tuple[typer.Typer, FakeLLMClient]:
    """Return the CLI wired to a scripted model and the given analyzers."""
    llm = FakeLLMClient(script)
    return build_app(lambda settings: llm, lambda: list(analyzers)), llm


def write_snippet(code: str = SNIPPET) -> str:
    """Write a snippet to a file in the test's folder and return its path."""
    Path("snippet.py").write_text(code, encoding="utf-8")
    return "snippet.py"


# Successful runs


def test_explain_prints_a_markdown_review_of_a_file() -> None:
    app, _ = fake_app(answer(issues=[ISSUE]))

    result = runner.invoke(app, ["explain", write_snippet()])

    assert result.exit_code == ExitCode.OK
    for heading in ("# Summary", "## Documentation", "## Issues", "## Improved code"):
        assert heading in result.stdout
    assert "- **high** bug, lines 1-2: No type checks." in result.stdout
    assert f"```python\n{GOOD_CODE}```" in result.stdout
    assert "## Warnings" not in result.stdout


def test_a_one_line_issue_names_a_single_line() -> None:
    one_line = ISSUE.model_copy(update={"line_end": 1})
    app, _ = fake_app(answer(issues=[one_line]))

    result = runner.invoke(app, ["explain", write_snippet()])

    assert "- **high** bug, line 1: No type checks." in result.stdout


def test_a_review_without_issues_says_so() -> None:
    app, _ = fake_app(answer())

    result = runner.invoke(app, ["explain", write_snippet()])

    assert "No issues found." in result.stdout


def test_explain_reads_stdin_when_no_file_is_given() -> None:
    app, llm = fake_app(answer())

    result = runner.invoke(app, ["explain"], input=SNIPPET)

    assert result.exit_code == ExitCode.OK
    assert "1 | def add(a, b):" in llm.requests[0].prompt


def test_windows_line_endings_on_stdin_become_plain_newlines() -> None:
    app, llm = fake_app(answer())

    runner.invoke(app, ["explain"], input=SNIPPET.replace("\n", "\r\n").encode())

    assert "\r" not in llm.requests[0].prompt
    assert "2 |     return a+b" in llm.requests[0].prompt


def test_json_output_has_the_analysis_warnings_and_metrics() -> None:
    app, _ = fake_app(answer(issues=[ISSUE]))

    result = runner.invoke(app, ["explain", write_snippet(), "--format", "json"])

    assert result.exit_code == ExitCode.OK
    report = json.loads(result.stdout)
    assert set(report) == {"analysis", "warnings", "metrics"}
    assert report["analysis"] == answer(issues=[ISSUE]).model_dump(mode="json")
    assert report["warnings"] == []
    assert report["metrics"]["rounds"] == 1
    assert report["metrics"]["cost_usd"] == pytest.approx(0.000035)


def test_json_mode_prints_no_metrics_line() -> None:
    app, _ = fake_app(answer())

    result = runner.invoke(app, ["explain", write_snippet(), "--format", "json"])

    assert "tokens" not in result.stderr


# Haiku's price for the fake client's 100 input and 50 output tokens: $0.000035.


def test_markdown_mode_prints_a_metrics_line_on_stderr() -> None:
    app, _ = fake_app(answer())

    result = runner.invoke(app, ["explain", write_snippet()])

    assert result.stderr.startswith("1 round | 100 in / 50 out tokens | $0.000035 | ")
    assert result.stderr.rstrip().endswith(" s")
    assert "tokens" not in result.stdout


def test_the_metrics_line_counts_rounds_and_escalations() -> None:
    app, _ = fake_app(answer(BAD_CODE), answer(BAD_CODE), answer())

    result = runner.invoke(app, ["explain", write_snippet()])

    # Two Haiku rounds at $0.000035 each, then Sonnet's price for the same tokens: $0.0007.
    assert "3 rounds (1 escalation) | 300 in / 150 out tokens | $0.00077 | " in result.stderr


def test_a_model_without_a_price_shows_the_cost_as_unknown() -> None:
    app, _ = fake_app(answer())

    result = runner.invoke(app, ["explain", write_snippet(), "--model", "claude-test"])

    assert "cost unknown (no price for claude-test)" in result.stderr


def test_the_model_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVASSIST_MODEL", "claude-from-env")
    app, llm = fake_app(answer())

    runner.invoke(app, ["explain", write_snippet(), "--model", "claude-from-flag"])

    assert llm.requests[0].model == "claude-from-flag"


def test_a_round_cap_of_one_stops_after_one_call_and_prints_warnings() -> None:
    app, llm = fake_app(answer(BAD_CODE))

    result = runner.invoke(app, ["explain", write_snippet(), "--max-rounds", "1"])

    assert result.exit_code == ExitCode.OK
    assert len(llm.requests) == 1
    assert "## Warnings" in result.stdout
    assert "- public function `add` changed from (a, b) to (x, y)" in result.stdout


def test_the_fence_is_longer_than_any_backticks_in_the_code() -> None:
    app, _ = fake_app(answer('def add(a, b):\n    """Like ```a + b```."""\n    return a + b\n'))

    result = runner.invoke(app, ["explain", write_snippet()])

    assert "````python\n" in result.stdout


def test_output_is_utf8_even_when_the_terminal_is_not() -> None:
    app, _ = fake_app(answer(summary="Adds two numbers → returns the sum."))

    result = CliRunner(charset="cp1252").invoke(app, ["explain", write_snippet()])

    assert result.exit_code == ExitCode.OK
    assert "→".encode() in result.stdout_bytes


def test_verbose_shows_escalations_on_stderr() -> None:
    app, _ = fake_app(answer(BAD_CODE), answer(BAD_CODE), answer())

    result = runner.invoke(app, ["explain", write_snippet(), "--verbose"])

    assert "escalating" in result.stderr


def test_escalations_are_hidden_without_verbose() -> None:
    app, _ = fake_app(answer(BAD_CODE), answer(BAD_CODE), answer())

    result = runner.invoke(app, ["explain", write_snippet()])

    assert "escalating" not in result.stderr


def test_version_prints_the_installed_version() -> None:
    app, _ = fake_app()

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == ExitCode.OK
    assert result.stdout.strip() == f"devassist {version('devassist')}"


# Exit code 2: usage and input problems


def test_an_empty_file_is_an_input_error() -> None:
    app, llm = fake_app()

    result = runner.invoke(app, ["explain", write_snippet("")])

    assert result.exit_code == ExitCode.INPUT
    assert "Error: The snippet is empty." in result.stderr
    assert llm.requests == []


def test_another_language_is_an_input_error() -> None:
    app, _ = fake_app()

    result = runner.invoke(app, ["explain", write_snippet(), "--language", "javascript"])

    assert result.exit_code == ExitCode.INPUT
    assert "Only Python is supported" in result.stderr


def test_a_missing_file_is_a_usage_error() -> None:
    app, llm = fake_app()

    result = runner.invoke(app, ["explain", "missing.py"])

    assert result.exit_code == ExitCode.INPUT
    assert llm.requests == []


def test_a_round_cap_below_one_is_a_usage_error() -> None:
    app, _ = fake_app()

    result = runner.invoke(app, ["explain", write_snippet(), "--max-rounds", "0"])

    assert result.exit_code == ExitCode.INPUT


def test_a_file_that_is_not_utf8_is_an_input_error() -> None:
    Path("latin1.py").write_bytes(b"name = '\xe9'\n")
    app, _ = fake_app()

    result = runner.invoke(app, ["explain", "latin1.py"])

    assert result.exit_code == ExitCode.INPUT
    assert "isn't UTF-8" in result.stderr


def test_an_unreadable_file_is_an_input_error(monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_snippet()

    def refuse(self: Path, encoding: str | None = None) -> str:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_text", refuse)
    app, _ = fake_app()

    result = runner.invoke(app, ["explain", path])

    assert result.exit_code == ExitCode.INPUT
    assert "Couldn't read snippet.py: Permission denied" in result.stderr


def test_stdin_that_is_not_utf8_is_an_input_error() -> None:
    app, _ = fake_app()

    result = runner.invoke(app, ["explain"], input=b"name = '\xe9'\n")

    assert result.exit_code == ExitCode.INPUT
    assert "isn't UTF-8" in result.stderr


def test_no_file_and_nothing_piped_in_is_an_input_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    app, _ = fake_app()

    result = runner.invoke(app, ["explain"])

    assert result.exit_code == ExitCode.INPUT
    assert "devassist explain path/to/file.py" in result.stderr


# Exit code 3: configuration problems


def test_a_missing_api_key_is_a_config_error_and_calls_no_api() -> None:
    # The real app: with no key it must stop before creating the Anthropic client.
    result = runner.invoke(build_app(), ["explain", write_snippet()])

    assert result.exit_code == ExitCode.CONFIG
    assert "ANTHROPIC_API_KEY" in result.stderr


def test_an_invalid_setting_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVASSIST_MAX_ROUNDS", "0")
    app, _ = fake_app()

    result = runner.invoke(app, ["explain", write_snippet()])

    assert result.exit_code == ExitCode.CONFIG
    assert "DEVASSIST_MAX_ROUNDS" in result.stderr


# Exit codes 4 and 5: model and analyzer problems


@pytest.mark.parametrize(
    "error",
    [LLMError("The API is overloaded."), SchemaValidationError("The API is overloaded.")],
    ids=["llm-error", "schema-error"],
)
def test_a_model_failure_exits_with_the_model_code(error: Exception) -> None:
    app, _ = fake_app(error)

    result = runner.invoke(app, ["explain", write_snippet()])

    assert result.exit_code == ExitCode.MODEL
    assert "Error: The API is overloaded." in result.stderr


def test_an_analyzer_failure_exits_with_the_analyzer_code() -> None:
    app, _ = fake_app(analyzers=[FailingAnalyzer()])

    result = runner.invoke(app, ["explain", write_snippet()])

    assert result.exit_code == ExitCode.ANALYZER
    assert "Error: ruff crashed." in result.stderr


def test_any_other_devassist_error_exits_with_the_general_code() -> None:
    def broken_factory(settings: object) -> FakeLLMClient:
        raise DevassistError("Something unexpected went wrong.")

    result = runner.invoke(build_app(broken_factory), ["explain", write_snippet()])

    assert result.exit_code == ExitCode.UNEXPECTED
    assert "Error: Something unexpected went wrong." in result.stderr
