from devassist.analyzers import RuffAnalyzer


def test_finds_an_unused_import_on_the_right_line() -> None:
    findings = RuffAnalyzer().analyze("import os\n\nx = 1\n")

    assert [(f.tool, f.code, f.line) for f in findings] == [("ruff", "F401", 1)]
    assert "os" in findings[0].message


def test_clean_code_has_no_findings() -> None:
    assert RuffAnalyzer().analyze("def add(a, b):\n    return a + b\n") == []


def test_broken_code_is_left_to_the_syntax_analyzer() -> None:
    assert RuffAnalyzer().analyze("def broken(:\n    pass\n") == []
