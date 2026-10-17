"""The interface every static analyzer implements, and the shared tool runner.

Submitted code is never executed, imported or passed to ``eval``. The ruff and
bandit analyzers write it to a temporary file and run the tool on that file
through ``run_tool``, which enforces the subprocess rules in one place.
"""

import subprocess
import sys
import tempfile
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from devassist.exceptions import AnalyzerError
from devassist.models import Finding

SNIPPET_FILENAME = "snippet.py"
ERROR_DETAIL_LIMIT = 500  # characters of tool output to include in an error message


class Analyzer(Protocol):
    """Anything that can check a snippet without running it and report what it finds."""

    name: str

    def analyze(self, code: str) -> list[Finding]:
        """Check the code without running it.

        Args:
            code: The Python source to check.

        Returns:
            Every finding, or an empty list if there are none.

        Raises:
            AnalyzerError: If the underlying tool times out, crashes or returns
                output that can't be read.
        """


def run_tool(
    module: str,
    args: Sequence[str],
    code: str,
    *,
    ok_exit_codes: Collection[int],
    timeout: float,
) -> str:
    """Run ``python -I -m <module>`` on the code and return what it printed.

    The code is written to ``snippet.py`` in a fresh temporary folder and the
    file is closed before the tool opens it, because Windows won't let a second
    process open a file that's still open. The tool runs inside that folder,
    and ``-I`` stops Python from importing modules from the current folder, so
    a stray file named ``ruff.py`` can't run in place of ruff.

    Args:
        module: The tool's module name, such as ``"ruff"``.
        args: Arguments placed before the snippet's file name.
        code: The Python source to check.
        ok_exit_codes: Exit codes that mean the tool ran normally. Both ruff
            and bandit exit with 1 when they find problems.
        timeout: Seconds to wait before giving up.

    Returns:
        The tool's standard output.

    Raises:
        AnalyzerError: If the tool can't start, times out or exits with an
            unexpected code.
    """
    with tempfile.TemporaryDirectory() as folder_name:
        folder = Path(folder_name)
        (folder / SNIPPET_FILENAME).write_text(code, encoding="utf-8")
        command = [sys.executable, "-I", "-m", module, *args, SNIPPET_FILENAME]
        try:
            completed = subprocess.run(
                command,
                cwd=folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AnalyzerError(f"{module} timed out after {timeout:g} seconds") from error
        except OSError as error:
            raise AnalyzerError(f"{module} could not start: {error}") from error
    if completed.returncode not in ok_exit_codes:
        detail = (completed.stderr or completed.stdout).strip()[:ERROR_DETAIL_LIMIT]
        raise AnalyzerError(f"{module} failed with exit code {completed.returncode}: {detail}")
    return completed.stdout


def parse_output[T](module: str, schema: TypeAdapter[T], output: str) -> T:
    """Check a tool's JSON output against the shape devassist expects.

    Args:
        module: The tool's module name, used in the error message.
        schema: The expected shape of the output.
        output: The tool's standard output.

    Returns:
        The output, parsed and validated.

    Raises:
        AnalyzerError: If the output isn't valid JSON or has an unexpected shape.
    """
    try:
        return schema.validate_json(output)
    except ValidationError as error:
        raise AnalyzerError(f"{module} returned output devassist couldn't read: {error}") from error
