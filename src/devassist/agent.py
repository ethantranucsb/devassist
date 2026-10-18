"""The LangGraph workflow: static checks, a draft, then verify and refine up to the round cap.

This is the only module that imports LangGraph. Each node is a plain function
of the state plus the dependencies it needs, so tests can call it directly.
``build_graph`` binds those dependencies with ``functools.partial``, so
LangGraph sees a function of the state alone. Nodes return only the fields
they change.
"""

import ast
import logging
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from functools import partial
from typing import Any, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from devassist.analyzers import Analyzer
from devassist.config import SUPPORTED_LANGUAGE, Settings
from devassist.llm import LLMClient, LLMRequest
from devassist.metrics import summarize
from devassist.models import Attempt, Finding, GraphState
from devassist.prompts import draft_prompt, refine_prompt, system_prompt
from devassist.verify import verification_problems

logger = logging.getLogger(__name__)

type Update = dict[str, object]
type Graph = CompiledStateGraph[GraphState]
# Called after each step with the node's name and the fields it changed.
type StepCallback = Callable[[str, Mapping[str, Any]], None]


def _analyze(code: str, analyzers: Sequence[Analyzer]) -> list[Finding]:
    """Run every analyzer on the code and collect their findings in order."""
    return [finding for analyzer in analyzers for finding in analyzer.analyze(code)]


def model_for_round(round_number: int, settings: Settings) -> str:
    """Pick the model for a round.

    Args:
        round_number: The round about to run, counting from 1.
        settings: The default model, the fallback model and the escalation round.

    Returns:
        The fallback model from the escalation round on, and the default model before it.
    """
    if round_number >= settings.escalation_round:
        return settings.fallback_model
    return settings.model


def _ask_model(round_number: int, prompt: str, llm: LLMClient, settings: Settings) -> Attempt:
    """Send one round's prompt to that round's model and record the answer."""
    model = model_for_round(round_number, settings)
    escalated = model != settings.model
    if escalated:
        logger.info("Round %d: escalating from %s to %s", round_number, settings.model, model)
    result = llm.generate(LLMRequest(model=model, system=system_prompt(), prompt=prompt))
    return Attempt(
        round=round_number,
        model=result.model,
        analysis=result.analysis,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        escalated=escalated,
    )


def _too_deeply_nested(code: str) -> bool:
    """Whether Python's parser gives up on the code because it nests too deeply.

    The limit depends on the OS: on Windows, about 3,000 chained ``+`` (some
    6,000 characters, well within the size limit) exhaust the parser's
    recursion and crash ruff and bandit as well. An ordinary syntax error is
    not rejected here: fixing it is part of the review.
    """
    try:
        ast.parse(code)
    except RecursionError:
        return True
    except SyntaxError:
        return False  # reported by the syntax analyzer and fixed by the model instead
    return False


def validate_input(state: GraphState, *, settings: Settings) -> Update:
    """Reject an empty, oversized, non-Python or too deeply nested snippet before anything runs."""
    if not state.snippet.strip():
        return {"error": "The snippet is empty."}
    if len(state.snippet) > settings.max_snippet_chars:
        return {
            "error": f"The snippet has {len(state.snippet):,} characters; "
            f"the limit is {settings.max_snippet_chars:,}."
        }
    if state.language.lower() != SUPPORTED_LANGUAGE:
        return {"error": f"Only Python is supported, not {state.language!r}."}
    if _too_deeply_nested(state.snippet):
        return {"error": "The snippet is nested too deeply for Python's parser to read."}
    return {}


def run_static_checks(state: GraphState, *, analyzers: Sequence[Analyzer]) -> Update:
    """Run every analyzer on the submitted snippet."""
    return {"findings": _analyze(state.snippet, analyzers)}


def draft(state: GraphState, *, llm: LLMClient, settings: Settings) -> Update:
    """Ask the model for its first answer, given the snippet and its findings."""
    attempt = _ask_model(1, draft_prompt(state.snippet, state.findings), llm, settings)
    return {"attempts": [attempt]}


def verify(state: GraphState, *, analyzers: Sequence[Analyzer]) -> Update:
    """Check the latest improved code: it must parse, add no findings and keep the interface."""
    improved_code = state.attempts[-1].analysis.improved_code
    problems = verification_problems(
        state.snippet, state.findings, improved_code, _analyze(improved_code, analyzers)
    )
    return {"problems": problems}


def refine(state: GraphState, *, llm: LLMClient, settings: Settings) -> Update:
    """Ask the model to fix the problems verify found in its latest answer."""
    latest = state.attempts[-1]
    prompt = refine_prompt(state.snippet, state.findings, latest.analysis, state.problems)
    return {"attempts": [_ask_model(latest.round + 1, prompt, llm, settings)]}


def finalize(state: GraphState) -> Update:
    """Return the latest answer, with any problems it still has as warnings."""
    return {"result": state.attempts[-1].analysis, "warnings": state.problems}


def route_after_validation(state: GraphState) -> str:
    """End the run if the input was rejected; otherwise go on to the static checks."""
    return END if state.error is not None else "run_static_checks"


def route_after_verify(state: GraphState, *, settings: Settings) -> str:
    """Finalize a pass or a failure on the last round; refine a failure with rounds left."""
    if not state.problems or state.attempts[-1].round >= settings.max_rounds:
        return "finalize"
    return "refine"


def step_limit(max_rounds: int) -> int:
    """Count the LangGraph steps in the longest possible run.

    The longest run is validate_input, run_static_checks, draft and verify,
    then refine and verify for each later round, then finalize. LangGraph
    also counts its own starting step.

    Args:
        max_rounds: The round cap.

    Returns:
        The step limit that lets the longest run finish, and nothing longer.
    """
    return 2 * max_rounds + 4


def build_graph(llm_client: LLMClient, analyzers: Sequence[Analyzer], settings: Settings) -> Graph:
    """Build and compile the graph, with its dependencies bound to the nodes.

    Args:
        llm_client: Answers the draft and refine prompts.
        analyzers: Check the snippet and every rewrite. Include ``SyntaxAnalyzer``,
            or a rewrite that doesn't parse can't be caught.
        settings: The models, the round cap, the escalation round and the size limit.

    Returns:
        The compiled graph. Its step limit is exactly what the longest run
        needs, so a routing mistake can't loop on paid model calls.
    """
    builder = StateGraph(GraphState)
    builder.add_node("validate_input", partial(validate_input, settings=settings))
    builder.add_node("run_static_checks", partial(run_static_checks, analyzers=analyzers))
    builder.add_node("draft", partial(draft, llm=llm_client, settings=settings))
    builder.add_node("verify", partial(verify, analyzers=analyzers))
    builder.add_node("refine", partial(refine, llm=llm_client, settings=settings))
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input", route_after_validation, ["run_static_checks", END]
    )
    builder.add_edge("run_static_checks", "draft")
    builder.add_edge("draft", "verify")
    builder.add_conditional_edges(
        "verify", partial(route_after_verify, settings=settings), ["refine", "finalize"]
    )
    builder.add_edge("refine", "verify")
    builder.add_edge("finalize", END)
    return builder.compile().with_config(recursion_limit=step_limit(settings.max_rounds))


def run_graph(
    graph: Graph,
    snippet: str,
    language: str = SUPPORTED_LANGUAGE,
    *,
    clock: Callable[[], float] = time.perf_counter,
    on_step: StepCallback | None = None,
) -> GraphState:
    """Run the graph on one snippet, timing the whole run.

    Args:
        graph: A graph from ``build_graph``.
        snippet: The submitted code.
        language: The snippet's language.
        clock: Returns the current time in seconds. Tests pass a fake one.
        on_step: Called after each step with the node's name and the fields
            it changed, for ``--trace``.

    Returns:
        The final state with its metrics. LangGraph streams plain dicts, so
        the last full state is validated back into a ``GraphState``.

    Raises:
        LLMError: If a model call fails.
    """
    start = clock()
    final: Mapping[str, Any] = {}
    stream = graph.stream(
        GraphState(snippet=snippet, language=language), stream_mode=["updates", "values"]
    )
    # Given a list of modes, stream() yields (mode, chunk) pairs, which its type hints
    # don't say. "updates" chunks hold each node's changes; "values" hold the whole state.
    for mode, chunk in cast(Iterator[tuple[str, dict[str, Any]]], stream):
        if mode == "values":
            final = chunk
        elif on_step is not None:
            for node, update in chunk.items():
                # A node that changed nothing streams None rather than {}.
                on_step(node, update or {})
    state = GraphState.model_validate(final)
    metrics = summarize(state.attempts, latency_seconds=clock() - start)
    return state.model_copy(update={"metrics": metrics})
