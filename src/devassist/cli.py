"""The command line: reads a snippet, runs the graph and prints the review.

This module only parses arguments, renders results and turns errors into exit
codes; the work happens in the graph. ``build_app`` takes the model client and
analyzer factories as arguments, so tests can run the real CLI against fakes.
"""

import io
import json
import logging
import re
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from enum import IntEnum, StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, TextIO

import typer
from pydantic import ValidationError

from devassist.agent import build_graph, run_graph
from devassist.analyzers import Analyzer, default_analyzers
from devassist.config import SUPPORTED_LANGUAGE, Settings
from devassist.exceptions import (
    AnalyzerError,
    ConfigError,
    DevassistError,
    InputError,
    LLMError,
)
from devassist.llm import LLMClient
from devassist.llm.anthropic_client import AnthropicClient
from devassist.models import Analysis, Issue

type LLMFactory = Callable[[Settings], LLMClient]
type AnalyzerFactory = Callable[[], Sequence[Analyzer]]


class ExitCode(IntEnum):
    """What the process exit code tells a script about the run."""

    OK = 0
    UNEXPECTED = 1
    INPUT = 2
    CONFIG = 3
    MODEL = 4
    ANALYZER = 5


EXIT_CODES: tuple[tuple[type[DevassistError], ExitCode], ...] = (
    (InputError, ExitCode.INPUT),
    (ConfigError, ExitCode.CONFIG),
    (LLMError, ExitCode.MODEL),
    (AnalyzerError, ExitCode.ANALYZER),
)


class OutputFormat(StrEnum):
    """How the review is printed."""

    MARKDOWN = "markdown"
    JSON = "json"


def _anthropic_client(settings: Settings) -> LLMClient:
    """Create the real model client, which needs the API key."""
    return AnthropicClient(settings.require_api_key())


def _stdin_is_terminal() -> bool:
    """Whether stdin is an interactive terminal rather than a pipe or a file."""
    return sys.stdin.isatty()


def _read_stdin() -> str:
    """Read piped input as UTF-8, turning Windows line endings into plain newlines.

    ``Path.read_text`` does the same conversion for files. Without it, a
    ``\\r\\n`` would be written back out as ``\\r\\r\\n`` on Windows, and the
    analyzers would count every line twice.
    """
    try:
        text = sys.stdin.buffer.read().decode("utf-8")
    except UnicodeDecodeError as error:
        raise InputError("The piped input isn't UTF-8 text.") from error
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_input(path: Path | None) -> str:
    """Read the snippet from the file, or from stdin when no file is given."""
    if path is not None:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise InputError(f"{path} isn't UTF-8 text.") from error
        except OSError as error:
            raise InputError(f"Couldn't read {path}: {error.strerror}.") from error
    if _stdin_is_terminal():
        raise InputError(
            "Pass a Python file or pipe code in, for example: devassist explain path/to/file.py"
        )
    return _read_stdin()


def _load_settings(model: str | None, max_rounds: int | None) -> Settings:
    """Load settings, then apply the command-line flags, which take priority."""
    try:
        settings = Settings()
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise ConfigError(f"Invalid setting. {problems}") from error
    flags = {"model": model, "max_rounds": max_rounds}
    return settings.model_copy(update={k: v for k, v in flags.items() if v is not None})


def _exit_code(error: DevassistError) -> ExitCode:
    """Pick the exit code for an error, by its place in the error hierarchy."""
    for error_type, code in EXIT_CODES:
        if isinstance(error, error_type):
            return code
    return ExitCode.UNEXPECTED


def _use_utf8(*streams: TextIO) -> None:
    """Write UTF-8 even where the terminal or a redirected file defaults to another encoding."""
    for stream in streams:
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


@contextmanager
def _log_to_stderr(verbose: bool) -> Iterator[None]:
    """Show devassist's log on stderr during one command: warnings, plus info with --verbose."""
    logger = logging.getLogger("devassist")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO if verbose else logging.WARNING)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def _fence(code: str) -> str:
    """Return a code fence longer than any run of backticks in the code."""
    longest = max((len(run) for run in re.findall(r"`+", code)), default=0)
    return "`" * max(3, longest + 1)


def _issue_line(issue: Issue) -> str:
    """Render one issue as a Markdown list item."""
    if issue.line_start == issue.line_end:
        lines = f"line {issue.line_start}"
    else:
        lines = f"lines {issue.line_start}-{issue.line_end}"
    return f"- **{issue.severity}** {issue.category}, {lines}: {issue.description}"


def render_markdown(analysis: Analysis, warnings: Sequence[str]) -> str:
    """Render the review as Markdown.

    Args:
        analysis: The final answer.
        warnings: Problems the improved code still has after the last round.

    Returns:
        The summary, documentation, issues and improved code, plus a warnings
        section when there are warnings.
    """
    fence = _fence(analysis.improved_code)
    lines = ["# Summary", "", analysis.summary, "", "## Documentation", "", analysis.documentation]
    lines += ["", "## Issues", ""]
    lines += [_issue_line(issue) for issue in analysis.issues] or ["No issues found."]
    lines += ["", "## Improved code", "", f"{fence}python", analysis.improved_code.rstrip("\n")]
    lines.append(fence)
    if warnings:
        lines += ["", "## Warnings", "", "The improved code still has these problems:", ""]
        lines += [f"- {warning}" for warning in warnings]
    return "\n".join(lines)


def render_json(analysis: Analysis, warnings: Sequence[str]) -> str:
    """Render the review as JSON, with the analysis and the warnings as separate keys."""
    report = {"analysis": analysis.model_dump(mode="json"), "warnings": list(warnings)}
    return json.dumps(report, indent=2, ensure_ascii=False)


def _show_version(value: bool) -> None:
    """Print the installed version and stop, for ``--version``."""
    if value:
        typer.echo(f"devassist {version('devassist')}")
        raise typer.Exit()


def build_app(
    make_llm: LLMFactory = _anthropic_client,
    make_analyzers: AnalyzerFactory = default_analyzers,
) -> typer.Typer:
    """Build the command-line app.

    Args:
        make_llm: Creates the model client from the settings.
        make_analyzers: Creates the static analyzers.

    Returns:
        The Typer app, with the ``explain`` command and ``--version``.
    """
    # Never print local variables in a crash report: one of them could be the API key.
    app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)

    @app.callback()
    def main(
        show_version: Annotated[
            bool,
            typer.Option(
                "--version",
                callback=_show_version,
                is_eager=True,
                help="Show the version and exit.",
            ),
        ] = False,
    ) -> None:
        """Document, review and improve Python snippets, checking every suggestion."""

    @app.command()
    def explain(
        path: Annotated[
            Path | None,
            typer.Argument(
                exists=True, dir_okay=False, help="The Python file to review. Omit to read stdin."
            ),
        ] = None,
        output_format: Annotated[
            OutputFormat, typer.Option("--format", help="How to print the review.")
        ] = OutputFormat.MARKDOWN,
        model: Annotated[
            str | None,
            typer.Option(help="Model for the rounds before escalation. Overrides DEVASSIST_MODEL."),
        ] = None,
        max_rounds: Annotated[
            int | None,
            typer.Option(min=1, help="Most model answers per run. Overrides DEVASSIST_MAX_ROUNDS."),
        ] = None,
        language: Annotated[
            str, typer.Option(help="The snippet's language. Only Python is supported.")
        ] = SUPPORTED_LANGUAGE,
        verbose: Annotated[
            bool, typer.Option("--verbose", "-v", help="Log escalations and retries to stderr.")
        ] = False,
    ) -> None:
        """Document a snippet, flag its issues and suggest a checked improvement."""
        _use_utf8(sys.stdout, sys.stderr)
        with _log_to_stderr(verbose):
            try:
                code = _read_input(path)
                settings = _load_settings(model, max_rounds)
                graph = build_graph(make_llm(settings), make_analyzers(), settings)
                state = run_graph(graph, code, language)
                if state.error is not None:
                    raise InputError(state.error)
            except DevassistError as error:
                typer.echo(f"Error: {error}", err=True)
                raise typer.Exit(_exit_code(error)) from error
        if state.result is None:
            raise RuntimeError("The graph finished without a result or an error.")
        render = render_json if output_format is OutputFormat.JSON else render_markdown
        typer.echo(render(state.result, state.warnings))

    return app


app = build_app()
