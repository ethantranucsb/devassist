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


def test_code_nested_too_deeply_gives_a_finding_instead_of_crashing() -> None:
    # About 6,000 characters: within the size limit, but past the parser's recursion limit.
    deep = "x = " + "+".join(["1"] * 3_000) + "\n"

    findings = SyntaxAnalyzer().analyze(deep)

    assert len(findings) == 1
    assert findings[0].code == "syntax-error"
    assert findings[0].message == "too deeply nested for Python's parser"
    assert findings[0].line == 1
