import logging
import os
from collections.abc import Sequence

import pytest
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START

from devassist.agent import (
    build_graph,
    draft,
    finalize,
    model_for_round,
    refine,
    route_after_validation,
    route_after_verify,
    run_graph,
    run_static_checks,
    step_limit,
    validate_input,
    verify,
)
from devassist.analyzers import Analyzer, default_analyzers
from devassist.config import Settings
from devassist.exceptions import LLMError
from devassist.llm import FakeLLMClient, LLMResult
from devassist.llm.fake import FAKE_INPUT_TOKENS, FAKE_OUTPUT_TOKENS, ScriptStep
from devassist.models import Analysis, Attempt, Finding, GraphState
from devassist.prompts import draft_prompt, refine_prompt, system_prompt

HAIKU = "haiku-test"
SONNET = "sonnet-test"
NODES = {"validate_input", "run_static_checks", "draft", "verify", "refine", "finalize"}

SNIPPET = "def add(a, b):\n    return a+b\n"
GOOD_CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
# Renames the parameters, so verify rejects it even with no analyzers.
BAD_CODE = "def add(x, y):\n    return x + y\n"
BAD_CODE_PROBLEM = "public function `add` changed from (a, b) to (x, y)"


class FakeAnalyzer(Analyzer):
    """Returns scripted findings for exact pieces of code and records what it was asked to check."""

    name = "fake"

    def __init__(self, findings: dict[str, list[Finding]] | None = None) -> None:
        self._findings = findings or {}
        self.seen: list[str] = []

    def analyze(self, code: str) -> list[Finding]:
        self.seen.append(code)
        return self._findings.get(code, [])


def make_settings(**overrides: object) -> Settings:
    """Return settings with test model names, whatever the environment or a .env file says."""
    values: dict[str, object] = {
        "model": HAIKU,
        "fallback_model": SONNET,
        "max_rounds": 3,
        "escalation_round": 3,
        "max_snippet_chars": 200,
    }
    values.update(overrides)
    return Settings(_env_file=None).model_copy(update=values)


def answer(code: str) -> Analysis:
    """Return a model answer whose improved code is the given code."""
    return Analysis(
        summary="Adds two numbers.",
        documentation="`add(a, b)` returns the sum.",
        issues=[],
        improved_code=code,
    )


def attempt(
    code: str, round_number: int = 1, model: str = HAIKU, escalated: bool = False
) -> Attempt:
    """Return a recorded round whose answer has the given improved code."""
    return Attempt(
        round=round_number,
        model=model,
        analysis=answer(code),
        input_tokens=FAKE_INPUT_TOKENS,
        output_tokens=FAKE_OUTPUT_TOKENS,
        escalated=escalated,
    )


def finding(code: str = "F401", line: int = 1, tool: str = "ruff") -> Finding:
    """Return a finding with the given rule code, line and tool."""
    return Finding(tool=tool, code=code, message=f"message for {code}", line=line)


def run(
    script: Sequence[ScriptStep],
    snippet: str = SNIPPET,
    language: str = "python",
    analyzers: Sequence[Analyzer] = (),
    **overrides: object,
) -> tuple[GraphState, FakeLLMClient]:
    """Run the compiled graph with a scripted model; return the final state and the fake."""
    llm = FakeLLMClient(script)
    graph = build_graph(llm, analyzers, make_settings(**overrides))
    return run_graph(graph, snippet, language), llm


def test_tests_run_with_langsmith_tracing_switched_off() -> None:
    tracing = [name for name in os.environ if name.upper().startswith(("LANGSMITH_", "LANGCHAIN_"))]

    assert tracing == []


# validate_input


@pytest.mark.parametrize("snippet", ["", "   \n\t"], ids=["empty", "whitespace"])
def test_validate_input_rejects_a_blank_snippet(snippet: str) -> None:
    update = validate_input(GraphState(snippet=snippet), settings=make_settings())

    assert update == {"error": "The snippet is empty."}


def test_validate_input_rejects_a_snippet_over_the_limit() -> None:
    update = validate_input(GraphState(snippet="x" * 201), settings=make_settings())

    assert update == {"error": "The snippet has 201 characters; the limit is 200."}


def test_validate_input_accepts_a_snippet_exactly_at_the_limit() -> None:
    assert validate_input(GraphState(snippet="x" * 200), settings=make_settings()) == {}


def test_validate_input_rejects_code_nested_too_deeply() -> None:
    # Past the parser's recursion limit on every OS (see test_syntax_analyzer.py),
    # with the size limit raised so the nesting check is what rejects it.
    deep = "x = " + "+".join(["1"] * 100_000) + "\n"
    settings = make_settings(max_snippet_chars=len(deep))

    update = validate_input(GraphState(snippet=deep), settings=settings)

    assert update == {"error": "The snippet is nested too deeply for Python's parser to read."}


def test_validate_input_leaves_ordinary_syntax_errors_to_the_model() -> None:
    state = GraphState(snippet="def add(a, b)\n    return a + b\n")

    assert validate_input(state, settings=make_settings()) == {}


def test_validate_input_rejects_other_languages() -> None:
    state = GraphState(snippet=SNIPPET, language="javascript")

    update = validate_input(state, settings=make_settings())

    assert update == {"error": "Only Python is supported, not 'javascript'."}


def test_validate_input_ignores_the_case_of_the_language() -> None:
    state = GraphState(snippet=SNIPPET, language="Python")

    assert validate_input(state, settings=make_settings()) == {}


# run_static_checks


def test_run_static_checks_collects_findings_from_every_analyzer() -> None:
    ruff = FakeAnalyzer({SNIPPET: [finding("F401")]})
    bandit = FakeAnalyzer({SNIPPET: [finding("B602", tool="bandit")]})

    update = run_static_checks(GraphState(snippet=SNIPPET), analyzers=[ruff, bandit])

    assert update == {"findings": [finding("F401"), finding("B602", tool="bandit")]}


# draft


def test_draft_sends_the_draft_prompt_to_the_default_model() -> None:
    llm = FakeLLMClient([answer(GOOD_CODE)])
    state = GraphState(snippet=SNIPPET, findings=[finding()])

    update = draft(state, llm=llm, settings=make_settings())

    request = llm.requests[0]
    assert request.model == HAIKU
    assert request.system == system_prompt()
    assert request.prompt == draft_prompt(SNIPPET, [finding()])
    assert update == {"attempts": [attempt(GOOD_CODE)]}


def test_an_attempt_records_the_model_that_answered_and_its_tokens() -> None:
    result = LLMResult(
        analysis=answer(GOOD_CODE), model="haiku-test-20261001", input_tokens=7, output_tokens=3
    )
    llm = FakeLLMClient([result])

    update = draft(GraphState(snippet=SNIPPET), llm=llm, settings=make_settings())

    expected = Attempt(
        round=1,
        model="haiku-test-20261001",
        analysis=answer(GOOD_CODE),
        input_tokens=7,
        output_tokens=3,
    )
    assert update == {"attempts": [expected]}


# verify


def test_verify_checks_the_latest_improved_code() -> None:
    analyzer = FakeAnalyzer()
    state = GraphState(
        snippet=SNIPPET, attempts=[attempt(BAD_CODE), attempt(GOOD_CODE, round_number=2)]
    )

    update = verify(state, analyzers=[analyzer])

    assert analyzer.seen == [GOOD_CODE]
    assert update == {"problems": []}


def test_verify_reports_a_new_finding() -> None:
    analyzer = FakeAnalyzer({GOOD_CODE: [finding("F841", line=2)]})
    state = GraphState(snippet=SNIPPET, attempts=[attempt(GOOD_CODE)])

    update = verify(state, analyzers=[analyzer])

    assert "F841" in str(update["problems"])


def test_verify_reports_a_changed_signature() -> None:
    state = GraphState(snippet=SNIPPET, attempts=[attempt(BAD_CODE)])

    assert verify(state, analyzers=[]) == {"problems": [BAD_CODE_PROBLEM]}


# refine and model_for_round


def test_refine_sends_the_previous_answer_and_its_problems() -> None:
    llm = FakeLLMClient([answer(GOOD_CODE)])
    state = GraphState(
        snippet=SNIPPET, findings=[finding()], attempts=[attempt(BAD_CODE)], problems=["a problem"]
    )

    update = refine(state, llm=llm, settings=make_settings())

    expected_prompt = refine_prompt(SNIPPET, [finding()], answer(BAD_CODE), ["a problem"])
    assert llm.requests[0].prompt == expected_prompt
    assert llm.requests[0].model == HAIKU
    assert update == {"attempts": [attempt(GOOD_CODE, round_number=2)]}


def test_refine_escalates_in_round_three_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLMClient([answer(GOOD_CODE)])
    state = GraphState(
        snippet=SNIPPET, attempts=[attempt(BAD_CODE), attempt(BAD_CODE, 2)], problems=["p"]
    )

    with caplog.at_level(logging.INFO, logger="devassist.agent"):
        update = refine(state, llm=llm, settings=make_settings())

    assert llm.requests[0].model == SONNET
    expected = attempt(GOOD_CODE, round_number=3, model=SONNET, escalated=True)
    assert update == {"attempts": [expected]}
    assert "escalating" in caplog.text
    assert SONNET in caplog.text


def test_no_escalation_is_logged_when_both_models_are_the_same(
    caplog: pytest.LogCaptureFixture,
) -> None:
    llm = FakeLLMClient([answer(GOOD_CODE)])
    state = GraphState(
        snippet=SNIPPET, attempts=[attempt(BAD_CODE), attempt(BAD_CODE, 2)], problems=["p"]
    )

    with caplog.at_level(logging.INFO, logger="devassist.agent"):
        refine(state, llm=llm, settings=make_settings(fallback_model=HAIKU))

    assert "escalating" not in caplog.text


@pytest.mark.parametrize(
    ("round_number", "expected"), [(1, HAIKU), (2, HAIKU), (3, SONNET), (4, SONNET)]
)
def test_model_for_round_switches_at_the_escalation_round(round_number: int, expected: str) -> None:
    assert model_for_round(round_number, make_settings()) == expected


def test_the_escalation_round_is_a_setting() -> None:
    assert model_for_round(2, make_settings(escalation_round=2)) == SONNET


# finalize


def test_finalize_returns_the_latest_answer_with_its_problems_as_warnings() -> None:
    state = GraphState(
        snippet=SNIPPET, attempts=[attempt(BAD_CODE), attempt(BAD_CODE, 2)], problems=["broken"]
    )

    assert finalize(state) == {"result": answer(BAD_CODE), "warnings": ["broken"]}


# Routing functions


def test_route_after_validation_ends_on_an_error() -> None:
    state = GraphState(snippet="", error="The snippet is empty.")

    assert route_after_validation(state) == END


def test_route_after_validation_continues_without_an_error() -> None:
    assert route_after_validation(GraphState(snippet=SNIPPET)) == "run_static_checks"


def test_route_after_verify_finalizes_a_pass() -> None:
    state = GraphState(snippet=SNIPPET, attempts=[attempt(GOOD_CODE)])

    assert route_after_verify(state, settings=make_settings()) == "finalize"


def test_route_after_verify_refines_a_failure_with_rounds_left() -> None:
    state = GraphState(snippet=SNIPPET, attempts=[attempt(BAD_CODE)], problems=[BAD_CODE_PROBLEM])

    assert route_after_verify(state, settings=make_settings()) == "refine"


def test_route_after_verify_finalizes_a_failure_on_the_last_round() -> None:
    state = GraphState(
        snippet=SNIPPET,
        attempts=[attempt(BAD_CODE, n) for n in (1, 2, 3)],
        problems=[BAD_CODE_PROBLEM],
    )

    assert route_after_verify(state, settings=make_settings()) == "finalize"


# Every route through the compiled graph


@pytest.mark.parametrize(
    ("snippet", "language"),
    [("", "python"), ("x = 1\n" * 50, "python"), (SNIPPET, "javascript")],
    ids=["empty", "oversized", "other-language"],
)
def test_invalid_input_ends_the_graph_before_any_check_or_model_call(
    snippet: str, language: str
) -> None:
    analyzer = FakeAnalyzer()

    state, llm = run([], snippet=snippet, language=language, analyzers=[analyzer])

    assert state.error is not None
    assert analyzer.seen == []
    assert llm.requests == []
    assert state.result is None


def test_a_draft_that_passes_is_returned_after_one_round() -> None:
    state, llm = run([answer(GOOD_CODE)])

    assert state.result == answer(GOOD_CODE)
    assert state.warnings == []
    assert state.error is None
    assert [a.model for a in state.attempts] == [HAIKU]


def test_a_failed_draft_is_refined_on_the_default_model() -> None:
    state, llm = run([answer(BAD_CODE), answer(GOOD_CODE)])

    assert state.result == answer(GOOD_CODE)
    assert state.warnings == []
    assert [a.model for a in state.attempts] == [HAIKU, HAIKU]
    assert BAD_CODE_PROBLEM in llm.requests[1].prompt


def test_round_three_escalates_to_the_fallback_model() -> None:
    state, llm = run([answer(BAD_CODE), answer(BAD_CODE), answer(GOOD_CODE)])

    assert [request.model for request in llm.requests] == [HAIKU, HAIKU, SONNET]
    assert [a.escalated for a in state.attempts] == [False, False, True]
    assert state.result == answer(GOOD_CODE)
    assert state.warnings == []


def test_failing_every_round_returns_the_last_answer_with_warnings() -> None:
    state, llm = run([answer(BAD_CODE)] * 3)

    assert len(llm.requests) == 3
    assert state.result == answer(BAD_CODE)
    assert state.warnings == [BAD_CODE_PROBLEM]


def test_a_round_cap_of_one_never_refines() -> None:
    state, llm = run([answer(BAD_CODE)], max_rounds=1)

    assert len(llm.requests) == 1
    assert state.warnings == [BAD_CODE_PROBLEM]


@pytest.mark.parametrize("max_rounds", [1, 3, 15])
def test_the_step_limit_fits_the_longest_run_exactly(max_rounds: int) -> None:
    llm = FakeLLMClient([answer(BAD_CODE)] * max_rounds * 2)
    graph = build_graph(llm, [], make_settings(max_rounds=max_rounds))

    longest = run_graph(graph, SNIPPET)
    one_step_short = graph.with_config(recursion_limit=step_limit(max_rounds) - 1)

    assert graph.config["recursion_limit"] == step_limit(max_rounds)
    assert len(longest.attempts) == max_rounds
    with pytest.raises(GraphRecursionError):
        run_graph(one_step_short, SNIPPET)


def test_run_graph_attaches_metrics_timed_by_the_clock() -> None:
    llm = FakeLLMClient([answer(BAD_CODE), answer(GOOD_CODE)])
    graph = build_graph(llm, [], make_settings())
    times = iter([10.0, 12.5])

    state = run_graph(graph, SNIPPET, clock=lambda: next(times))

    assert state.metrics is not None
    assert state.metrics.latency_seconds == 2.5
    assert state.metrics.rounds == 2
    assert state.metrics.input_tokens == 2 * FAKE_INPUT_TOKENS


def test_run_graph_reports_each_step_in_order() -> None:
    llm = FakeLLMClient([answer(BAD_CODE), answer(GOOD_CODE)])
    graph = build_graph(llm, [], make_settings())
    steps: list[tuple[str, dict[str, object]]] = []

    run_graph(graph, SNIPPET, on_step=lambda node, update: steps.append((node, dict(update))))

    assert [node for node, _ in steps] == [
        "validate_input",
        "run_static_checks",
        "draft",
        "verify",
        "refine",
        "verify",
        "finalize",
    ]
    assert steps[0] == ("validate_input", {})
    assert steps[3] == ("verify", {"problems": [BAD_CODE_PROBLEM]})


def test_rejected_input_reports_only_the_validation_step() -> None:
    graph = build_graph(FakeLLMClient([]), [], make_settings())
    steps: list[tuple[str, dict[str, object]]] = []

    run_graph(graph, "", on_step=lambda node, update: steps.append((node, dict(update))))

    assert steps == [("validate_input", {"error": "The snippet is empty."})]


def test_rejected_input_still_gets_metrics() -> None:
    state, _ = run([], snippet="")

    assert state.metrics is not None
    assert state.metrics.rounds == 0
    assert state.metrics.cost_usd == 0.0


def test_a_model_error_propagates_out_of_the_graph() -> None:
    with pytest.raises(LLMError, match="overloaded"):
        run([LLMError("The API is overloaded.")])


def test_the_graph_has_exactly_the_six_nodes() -> None:
    graph = build_graph(FakeLLMClient([]), [], make_settings())

    assert set(graph.get_graph().nodes) - {START, END} == NODES


def test_real_analyzers_catch_a_syntax_error_and_refine_fixes_it() -> None:
    broken = "def add(a, b)\n    return a + b\n"

    state, llm = run([answer(broken), answer(GOOD_CODE)], analyzers=default_analyzers())

    assert len(llm.requests) == 2
    assert "syntax error on line 1" in llm.requests[1].prompt
    assert state.result == answer(GOOD_CODE)
    assert state.warnings == []
