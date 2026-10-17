from devassist.analyzers import SyntaxAnalyzer


def test_valid_code_has_no_findings() -> None:
    assert SyntaxAnalyzer().analyze("def add(a, b):\n    return a + b\n") == []


def test_broken_code_gives_one_syntax_finding() -> None:
    findings = SyntaxAnalyzer().analyze("def broken(:\n    pass\n")

    assert len(findings) == 1
    assert findings[0].tool == "syntax"
    assert findings[0].code == "syntax-error"
    assert findings[0].line == 1


def test_syntax_finding_points_at_the_broken_line() -> None:
    code = "x = 1\ny = 2\nif x > y\n    print(x)\n"

    findings = SyntaxAnalyzer().analyze(code)

    assert findings[0].line == 3
