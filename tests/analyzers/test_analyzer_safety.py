import subprocess
from pathlib import Path

import pytest

from devassist.analyzers import Analyzer, RuffAnalyzer, default_analyzers
from devassist.exceptions import AnalyzerError

ALL_ANALYZERS = default_analyzers()
ANALYZER_IDS = [analyzer.name for analyzer in ALL_ANALYZERS]


@pytest.mark.parametrize("analyzer", ALL_ANALYZERS, ids=ANALYZER_IDS)
def test_submitted_code_is_never_executed(analyzer: Analyzer, tmp_path: Path) -> None:
    sentinel = tmp_path / "ran.txt"
    code = f"from pathlib import Path\n\nPath({str(sentinel)!r}).write_text('ran')\n"

    analyzer.analyze(code)

    assert not sentinel.exists()


@pytest.mark.parametrize("analyzer", ALL_ANALYZERS, ids=ANALYZER_IDS)
def test_non_ascii_code_is_handled(analyzer: Analyzer) -> None:
    assert analyzer.analyze('greeting = "café ☕"\n') == []


class FakeRun:
    """A stand-in for subprocess.run that records each call and returns a fixed result."""

    def __init__(self, returncode: int = 0, stdout: str = "[]", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def test_subprocess_call_follows_the_security_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun()
    monkeypatch.setattr(subprocess, "run", fake)

    RuffAnalyzer().analyze("x = 1\n")

    [(command, kwargs)] = fake.calls
    assert command[1:4] == ["-I", "-m", "ruff"]
    assert kwargs.get("shell", False) is False
    assert isinstance(kwargs["timeout"], float) and kwargs["timeout"] > 0
    assert kwargs["encoding"] == "utf-8"
    assert Path(str(kwargs["cwd"])) != Path.cwd()


def test_timeout_raises_analyzer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def time_out(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=30)

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(AnalyzerError, match="timed out"):
        RuffAnalyzer().analyze("x = 1\n")


def test_tool_that_cannot_start_raises_analyzer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_to_start(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("python not found")

    monkeypatch.setattr(subprocess, "run", fail_to_start)

    with pytest.raises(AnalyzerError, match="could not start"):
        RuffAnalyzer().analyze("x = 1\n")


def test_unexpected_exit_code_raises_analyzer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(returncode=2, stderr="internal error"))

    with pytest.raises(AnalyzerError, match="exit code 2"):
        RuffAnalyzer().analyze("x = 1\n")


def test_unreadable_output_raises_analyzer_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", FakeRun(stdout="this is not JSON"))

    with pytest.raises(AnalyzerError, match="couldn't read"):
        RuffAnalyzer().analyze("x = 1\n")
