from devassist.analyzers import RuffAnalyzer


def test_finds_an_unused_import_on_the_right_line() -> None:
    findings = RuffAnalyzer().analyze("import os\n\nx = 1\n")

    assert [(f.tool, f.code, f.line) for f in findings] == [("ruff", "F401", 1)]
    assert "os" in findings[0].message


def test_clean_code_has_no_findings() -> None:
    assert RuffAnalyzer().analyze("def add(a, b):\n    return a + b\n") == []


def test_broken_code_is_left_to_the_syntax_analyzer() -> None:
    assert RuffAnalyzer().analyze("def broken(:\n    pass\n") == []


def test_a_noqa_comment_cannot_hide_a_finding() -> None:
    # A rewrite could otherwise silence the very check that verifies it.
    code = "def add(tag, tags=[]):  # noqa: B006\n    tags.append(tag)\n    return tags\n"

    findings = RuffAnalyzer().analyze(code)

    assert [(f.code, f.line) for f in findings] == [("B006", 1)]


def test_a_blanket_noqa_comment_cannot_hide_a_finding() -> None:
    assert [f.code for f in RuffAnalyzer().analyze("import os  # noqa\n")] == ["F401"]
