from textwrap import dedent

from devassist.models import Finding
from devassist.verify import public_signatures, verification_problems

ORIGINAL = dedent("""\
    import os


    def run(cmd, *, shell=False):
        os.system(cmd)


    def _helper():
        pass


    class Runner:
        def __init__(self, name):
            self.name = name

        def go(self, cmd):
            run(cmd)
""")

# The same public interface, with type hints added, a default changed and the
# private helper removed. All of these are allowed.
TYPED = dedent("""\
    import subprocess


    def run(cmd: str, *, shell: bool = True) -> None:
        subprocess.run(cmd, check=True)


    class Runner:
        def __init__(self, name: str) -> None:
            self.name = name

        def go(self, cmd: str) -> None:
            run(cmd)
""")


def finding(code: str = "F401", line: int = 1, tool: str = "ruff") -> Finding:
    """Return a finding with the given rule code, line and tool."""
    return Finding(tool=tool, code=code, message=f"message for {code}", line=line)


def test_public_signatures_lists_public_functions_classes_and_methods() -> None:
    assert public_signatures(ORIGINAL) == {
        "run": "(cmd, *, shell)",
        "Runner": "class",
        "Runner.__init__": "(self, name)",
        "Runner.go": "(self, cmd)",
    }


def test_signatures_show_every_kind_of_parameter() -> None:
    signatures = public_signatures("def f(a, /, b, *args, c, **kwargs):\n    pass\n")

    assert signatures == {"f": "(a, /, b, *args, c, **kwargs)"}


def test_signatures_of_unparseable_code_are_none() -> None:
    assert public_signatures("def broken(:\n    pass\n") is None


def test_rewrite_with_type_hints_and_no_private_helper_passes() -> None:
    assert verification_problems(ORIGINAL, [], TYPED, []) == []


def test_syntax_error_in_the_improved_code_is_a_problem() -> None:
    broken = [Finding(tool="syntax", code="syntax-error", message="expected ':'", line=3)]

    problems = verification_problems(ORIGINAL, [], "def run(cmd)\n", broken)

    assert problems == ["syntax error on line 3: expected ':'"]


def test_a_new_finding_is_a_problem() -> None:
    problems = verification_problems(
        ORIGINAL, [finding("F401")], TYPED, [finding("F401"), finding("F841", line=6)]
    )

    assert len(problems) == 1
    assert "F841" in problems[0]
    assert "line 6" in problems[0]


def test_the_same_finding_on_a_different_line_is_not_new() -> None:
    assert verification_problems(ORIGINAL, [finding(line=1)], TYPED, [finding(line=3)]) == []


def test_an_extra_copy_of_an_existing_rule_is_new() -> None:
    problems = verification_problems(
        ORIGINAL,
        [finding("F841", line=2)],
        TYPED,
        [finding("F841", line=2), finding("F841", line=9)],
    )

    assert len(problems) == 1
    assert "F841" in problems[0]
    assert "1 more" in problems[0]


def test_fewer_findings_pass() -> None:
    original_findings = [finding("B602", tool="bandit"), finding("F401")]

    assert verification_problems(ORIGINAL, original_findings, TYPED, []) == []


def test_a_missing_public_function_is_a_problem() -> None:
    without_run = TYPED.replace("def run(", "def execute(")

    problems = verification_problems(ORIGINAL, [], without_run, [])

    assert problems == ["public function `run` is missing"]


def test_a_renamed_parameter_is_a_problem() -> None:
    renamed = TYPED.replace("def run(cmd: str", "def run(command: str")

    problems = verification_problems(ORIGINAL, [], renamed, [])

    assert problems == ["public function `run` changed from (cmd, *, shell) to (command, *, shell)"]


def test_a_missing_public_method_is_a_problem() -> None:
    without_go = TYPED.split("    def go(")[0]

    problems = verification_problems(ORIGINAL, [], without_go, [])

    assert problems == ["public method `Runner.go` is missing"]


def test_unparseable_original_skips_the_signature_check() -> None:
    broken_original = "def run(cmd)\n    os.system(cmd)\n"
    original_findings = [finding("syntax-error", tool="syntax")]

    assert verification_problems(broken_original, original_findings, TYPED, []) == []
