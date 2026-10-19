"""Security findings from bandit."""

from pydantic import BaseModel, TypeAdapter

from devassist.analyzers.base import Analyzer, parse_output, run_tool
from devassist.config import ANALYZER_TIMEOUT_SECONDS
from devassist.exceptions import AnalyzerError
from devassist.models import Finding

BANDIT_OK_EXIT_CODES = frozenset({0, 1})
BANDIT_SYNTAX_ERROR_REASON = "syntax error"


class _Result(BaseModel):
    test_id: str
    issue_severity: str
    issue_text: str
    line_number: int


class _Error(BaseModel):
    reason: str


class _Report(BaseModel):
    results: list[_Result]
    errors: list[_Error]


_REPORT = TypeAdapter(_Report)


class BanditAnalyzer(Analyzer):
    """Runs bandit's security checks on the snippet.

    Syntax errors are skipped, because ``SyntaxAnalyzer`` already reports them.
    ``# nosec`` comments are ignored, so neither a snippet nor a model's rewrite
    can switch off the check that verifies it.
    """

    name = "bandit"

    def __init__(self, timeout: float = ANALYZER_TIMEOUT_SECONDS) -> None:
        """Create the analyzer.

        Args:
            timeout: Seconds to wait for bandit before giving up.
        """
        self._timeout = timeout

    def analyze(self, code: str) -> list[Finding]:
        """Check the code for security problems without running it.

        Args:
            code: The Python source to check.

        Returns:
            One finding per bandit result, with the severity at the start of
            the message, such as ``[HIGH]``.

        Raises:
            AnalyzerError: If bandit times out, crashes, prints unreadable
                output or reports an error other than a syntax error.
        """
        output = run_tool(
            "bandit",
            ["--format", "json", "--quiet", "--ignore-nosec"],
            code,
            ok_exit_codes=BANDIT_OK_EXIT_CODES,
            timeout=self._timeout,
        )
        report = parse_output("bandit", _REPORT, output)
        problems = [
            error.reason
            for error in report.errors
            if BANDIT_SYNTAX_ERROR_REASON not in error.reason.lower()
        ]
        if problems:
            raise AnalyzerError(f"bandit could not check the code: {'; '.join(problems)}")
        return [
            Finding(
                tool=self.name,
                code=result.test_id,
                message=f"[{result.issue_severity}] {result.issue_text}",
                line=result.line_number,
            )
            for result in report.results
        ]
