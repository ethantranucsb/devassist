import pytest

from devassist.analyzers import BanditAnalyzer
from devassist.exceptions import AnalyzerError

SHELL_CODE = "import os\nimport subprocess\n\ndef run(cmd):\n    subprocess.call(cmd, shell=True)\n"


def test_finds_shell_true_with_its_severity() -> None:
    findings = BanditAnalyzer().analyze(SHELL_CODE)

    by_code = {f.code: f for f in findings}
    assert by_code["B602"].line == 5
    assert by_code["B602"].message.startswith("[HIGH]")
    assert by_code["B602"].tool == "bandit"
    assert by_code["B404"].line == 2


def test_clean_code_has_no_findings() -> None:
    assert BanditAnalyzer().analyze("def add(a, b):\n    return a + b\n") == []


def test_broken_code_is_left_to_the_syntax_analyzer() -> None:
    assert BanditAnalyzer().analyze("def broken(:\n    pass\n") == []


def test_other_bandit_errors_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    report = '{"results": [], "errors": [{"reason": "could not read file"}]}'
    monkeypatch.setattr(
        "devassist.analyzers.bandit_analyzer.run_tool", lambda *args, **kwargs: report
    )

    with pytest.raises(AnalyzerError, match="could not read file"):
        BanditAnalyzer().analyze("x = 1\n")
