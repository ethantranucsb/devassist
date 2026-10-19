"""The LangGraph workflow: static checks, the default model's loop, a review, the fallback's loop.

The default model drafts, and refines until its rewrite passes verify or it
runs out of tries. A pass goes to the review, where a second model looks
for issues the first answer missed; any it finds go to the fallback model,
which adds and fixes them and refines until verify passes or it runs out of
tries. A default model that runs out of tries hands over to the fallback
model directly, without a review (docs/decisions.md D19).

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
from devassist.models import Analysis, Attempt, Finding, GraphState, Review, ReviewCall
from devassist.prompts import (
    draft_prompt,
    refine_prompt,
    review_prompt,
    review_system_prompt,
    revise_prompt,
    system_prompt,
)
from devassist.verify import verification_problems

logger = logging.getLogger(__name__)

type Update = dict[str, object]
type Graph = CompiledStateGraph[GraphState]
# Called after each step with the node's name and the fields it changed.
type StepCallback = Callable[[str, Mapping[str, Any]], None]


def _analyze(code: str, analyzers: Sequence[Analyzer]) -> list[Finding]:
    """Run every analyzer on the code and collect their findings in order."""
    return [finding for analyzer in analyzers for finding in analyzer.analyze(code)]


def _ask_model(
    round_number: int, prompt: str, llm: LLMClient, settings: Settings, *, fallback: bool
) -> Attempt:
    """Send one round's prompt to the default model, or to the fallback model, and record it."""
    model = settings.fallback_model if fallback else settings.model
    request = LLMRequest(model=model, system=system_prompt(), prompt=prompt, output_type=Analysis)
    result = llm.generate(request)
    return Attempt(
        round=round_number,
        model=result.model,
        analysis=result.output,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        fallback=fallback,
        escalated=fallback and model != settings.model,
        retried=result.retried,
    )


def _tries(state: GraphState) -> int:
    """Count the latest answer's model's tries: the default model's, or the fallback's."""
    fallback = state.attempts[-1].fallback
    return sum(1 for attempt in state.attempts if attempt.fallback == fallback)


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
    """Ask the default model for its first answer, given the snippet and its findings."""
    prompt = draft_prompt(state.snippet, state.findings)
    return {"attempts": [_ask_model(1, prompt, llm, settings, fallback=False)]}


def verify(state: GraphState, *, analyzers: Sequence[Analyzer]) -> Update:
    """Check the latest improved code: it must parse, add no findings and keep the interface."""
    improved_code = state.attempts[-1].analysis.improved_code
    problems = verification_problems(
        state.snippet, state.findings, improved_code, _analyze(improved_code, analyzers)
    )
    return {"problems": problems}


def review(state: GraphState, *, llm: LLMClient, settings: Settings) -> Update:
    """Ask the review model for real issues the latest answer missed."""
    issues = state.attempts[-1].analysis.issues
    logger.info("Second review on %s", settings.review_model)
    result = llm.generate(
        LLMRequest(
            model=settings.review_model,
            system=review_system_prompt(),
            prompt=review_prompt(state.snippet, issues),
            output_type=Review,
        )
    )
    call = ReviewCall(
        model=result.model,
        review=result.output,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        retried=result.retried,
    )
    return {"review": call}


def refine(state: GraphState, *, llm: LLMClient, settings: Settings) -> Update:
    """Ask the same model as the latest answer to fix the problems verify found in it."""
    latest = state.attempts[-1]
    prompt = refine_prompt(state.snippet, state.findings, latest.analysis, state.problems)
    attempt = _ask_model(latest.round + 1, prompt, llm, settings, fallback=latest.fallback)
    return {"attempts": [attempt]}


def escalate(state: GraphState, *, llm: LLMClient, settings: Settings) -> Update:
    """Hand the snippet to the fallback model for its first try.

    After a review that found missed issues, the fallback model adds and
    fixes them. After the default model ran out of tries, it fixes the
    problems verify found instead.
    """
    latest = state.attempts[-1]
    round_number = latest.round + 1
    if not state.problems and state.review is not None:
        missed = state.review.review.missed_issues
        prompt = revise_prompt(state.snippet, state.findings, latest.analysis, missed)
    else:
        prompt = refine_prompt(state.snippet, state.findings, latest.analysis, state.problems)
    if settings.fallback_model != settings.model:
        logger.info(
            "Round %d: escalating from %s to %s",
            round_number,
            settings.model,
            settings.fallback_model,
        )
    return {"attempts": [_ask_model(round_number, prompt, llm, settings, fallback=True)]}


def finalize(state: GraphState) -> Update:
    """Return the latest answer, with any problems it still has as warnings."""
    return {"result": state.attempts[-1].analysis, "warnings": state.problems}


def route_after_validation(state: GraphState) -> str:
    """End the run if the input was rejected; otherwise go on to the static checks."""
    return END if state.error is not None else "run_static_checks"


def route_after_verify(state: GraphState, *, settings: Settings) -> str:
    """Route a rewrite by whether it passed, and by whose try it was.

    A failure is refined by the same model while it has tries left. When the
    default model runs out, the fallback model takes over; when the fallback
    runs out, the run finalizes with warnings. The default model's pass goes
    to the review, when it's on, at most once; the fallback model's pass is final.
    """
    fallback = state.attempts[-1].fallback
    if state.problems:
        if _tries(state) < settings.max_rounds:
            return "refine"
        return "finalize" if fallback else "escalate"
    if settings.review and not fallback and state.review is None:
        return "review"
    return "finalize"


def route_after_review(state: GraphState) -> str:
    """Escalate to add and fix any missed issues; finalize if the review found none."""
    if state.review is not None and state.review.review.missed_issues:
        return "escalate"
    return "finalize"


def step_limit(max_rounds: int, *, review: bool = False) -> int:
    """Count the LangGraph steps in the longest possible run.

    The longest run is validate_input and run_static_checks, every try of
    the default model, each followed by verify, the review if it's on, then
    every try of the fallback model, each followed by verify, then finalize.
    LangGraph also counts its own starting step.

    Args:
        max_rounds: The most tries for each model.
        review: Whether the second review is on.

    Returns:
        The step limit that lets the longest run finish, and nothing longer.
    """
    return 4 * max_rounds + 4 + (1 if review else 0)


def build_graph(llm_client: LLMClient, analyzers: Sequence[Analyzer], settings: Settings) -> Graph:
    """Build and compile the graph, with its dependencies bound to the nodes.

    The review node is always part of the graph, so the diagram shows the
    whole design; the review setting decides whether a run ever reaches it.

    Args:
        llm_client: Answers the draft, refine, review and escalate prompts.
        analyzers: Check the snippet and every rewrite. Include ``SyntaxAnalyzer``,
            or a rewrite that doesn't parse can't be caught.
        settings: The models, the tries for each, the size limit and the review.

    Returns:
        The compiled graph. Its step limit is exactly what the longest run
        needs, so a routing mistake can't loop on paid model calls.
    """
    builder = StateGraph(GraphState)
    builder.add_node("validate_input", partial(validate_input, settings=settings))
    builder.add_node("run_static_checks", partial(run_static_checks, analyzers=analyzers))
    builder.add_node("draft", partial(draft, llm=llm_client, settings=settings))
    builder.add_node("verify", partial(verify, analyzers=analyzers))
    builder.add_node("review", partial(review, llm=llm_client, settings=settings))
    builder.add_node("refine", partial(refine, llm=llm_client, settings=settings))
    builder.add_node("escalate", partial(escalate, llm=llm_client, settings=settings))
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input", route_after_validation, ["run_static_checks", END]
    )
    builder.add_edge("run_static_checks", "draft")
    builder.add_edge("draft", "verify")
    builder.add_conditional_edges(
        "verify",
        partial(route_after_verify, settings=settings),
        ["refine", "review", "escalate", "finalize"],
    )
    builder.add_conditional_edges("review", route_after_review, ["escalate", "finalize"])
    builder.add_edge("refine", "verify")
    builder.add_edge("escalate", "verify")
    builder.add_edge("finalize", END)
    limit = step_limit(settings.max_rounds, review=settings.review)
    return builder.compile().with_config(recursion_limit=limit)


def mermaid_diagram(graph: Graph) -> str:
    """Draw the compiled graph as a Mermaid flowchart, for the README.

    Dashed arrows are conditional routes. The diagram comes from the graph
    itself, and a test checks that the README's copy matches it.

    Args:
        graph: A graph from ``build_graph``.

    Returns:
        The Mermaid source, ending with a newline.
    """
    return graph.get_graph().draw_mermaid()


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
    metrics = summarize(state.attempts, latency_seconds=clock() - start, review=state.review)
    return state.model_copy(update={"metrics": metrics})
