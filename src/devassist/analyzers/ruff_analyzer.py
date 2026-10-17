"""Lint findings from ruff."""

from pydantic import BaseModel, TypeAdapter

from devassist.analyzers.base import Analyzer, parse_output, run_tool
from devassist.config import (
    ANALYZER_TIMEOUT_SECONDS,
    RUFF_IGNORE,
    RUFF_SELECT,
    RUFF_TARGET_VERSION,
)
from devassist.models import Finding

RUFF_OK_EXIT_CODES = frozenset({0, 1})
RUFF_SYNTAX_ERROR_CODE = "invalid-syntax"


class _Location(BaseModel):
    row: int


class _Diagnostic(BaseModel):
    code: str | None
    message: str
    location: _Location


_DIAGNOSTICS = TypeAdapter(list[_Diagnostic])


class RuffAnalyzer(Analyzer):
    """Runs ruff's linter on the snippet.

    Syntax errors are skipped, because ``SyntaxAnalyzer`` already reports them.
    """

    name = "ruff"

    def __init__(self, timeout: float = ANALYZER_TIMEOUT_SECONDS) -> None:
        """Create the analyzer.

        Args:
            timeout: Seconds to wait for ruff before giving up.
        """
        self._timeout = timeout

    def analyze(self, code: str) -> list[Finding]:
        """Lint the code without running it.

        Args:
            code: The Python source to check.

        Returns:
            One finding per ruff diagnostic, excluding syntax errors.

        Raises:
            AnalyzerError: If ruff times out, crashes or prints unreadable output.
        """
        output = run_tool(
            "ruff",
            [
                "check",
                "--isolated",
                "--no-cache",
                "--output-format=json",
                f"--target-version={RUFF_TARGET_VERSION}",
                f"--select={','.join(RUFF_SELECT)}",
                f"--ignore={','.join(RUFF_IGNORE)}",
            ],
            code,
            ok_exit_codes=RUFF_OK_EXIT_CODES,
            timeout=self._timeout,
        )
        findings = []
        for diagnostic in parse_output("ruff", _DIAGNOSTICS, output):
            if diagnostic.code is None or diagnostic.code == RUFF_SYNTAX_ERROR_CODE:
                continue
            findings.append(
                Finding(
                    tool=self.name,
                    code=diagnostic.code,
                    message=diagnostic.message,
                    line=diagnostic.location.row,
                )
            )
        return findings
