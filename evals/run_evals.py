"""Run the eval: every case, several times, under each configuration, then judge D8's bar.

From the project root:

    uv run python -m evals.run_evals --dry-run   # estimate the cost; no API calls
    uv run python -m evals.run_evals --smoke     # the 12 smoke cases
    uv run python -m evals.run_evals             # all 30 cases

Each run calls the graph directly, as the CLI does; nothing here executes the
submitted code. Each run's raw record is appended to
``results/<label>-runs.jsonl`` as soon as it finishes, so an interrupted eval
keeps what it paid for. The summary JSON and the per-case CSV are written at
the end.
"""

import argparse
import csv
import json
import logging
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from devassist.agent import Graph, build_graph, model_for_round, run_graph
from devassist.analyzers import Analyzer, default_analyzers
from devassist.config import LangSmithStatus, Settings, langsmith_status
from devassist.exceptions import DevassistError
from devassist.llm import LLMClient
from devassist.llm.anthropic_client import AnthropicClient, output_schema
from devassist.metrics import TOKENS_PER_MILLION, price_for
from devassist.models import Analysis
from devassist.prompts import draft_prompt, system_prompt
from evals.cases import EvalCase, load_cases
from evals.configurations import CONFIGURATIONS, Configuration, settings_for
from evals.scoring import RunRecord, reaches_model
from evals.summary import CSV_COLUMNS, CsvRow, build_summary, case_rows, format_table, render_report

logger = logging.getLogger(__name__)

type LLMFactory = Callable[[Settings], LLMClient]
type AnalyzerFactory = Callable[[], Sequence[Analyzer]]
type Clock = Callable[[], float]

RESULTS_DIR = Path(__file__).with_name("results")
DEFAULT_RUNS = 3
DEFAULT_WORKERS = 4
ERROR_EXIT_CODE = 1

# Rough token arithmetic for --dry-run. The smoke run measures the real numbers.
CHARS_PER_TOKEN = 4
OUTPUT_TOKENS_PER_CODE_TOKEN = (
    3  # the documentation and the full rewrite, each about the code's size
)
OUTPUT_TOKENS_FIXED = 400  # the summary and the issues


def _anthropic_client(settings: Settings) -> LLMClient:
    """Create the real model client, which needs the API key."""
    return AnthropicClient(settings.require_api_key())


def build_graphs(
    configurations: Sequence[Configuration],
    base_settings: Settings,
    *,
    make_llm: LLMFactory,
    make_analyzers: AnalyzerFactory,
) -> dict[str, Graph]:
    """Build one graph per configuration, before any run starts.

    Args:
        configurations: The configurations to run.
        base_settings: The loaded settings, which supply the API key.
        make_llm: Creates a model client from a configuration's settings.
        make_analyzers: Creates the static analyzers.

    Returns:
        Each configuration's graph, by name, in the order given.

    Raises:
        DevassistError: If a client can't be created, such as when the API key is missing.
    """
    graphs = {}
    for configuration in configurations:
        settings = settings_for(configuration, base_settings)
        graphs[configuration.name] = build_graph(make_llm(settings), make_analyzers(), settings)
    return graphs


def run_case(
    graph: Graph, case: EvalCase, config: str, run: int, *, clock: Clock = time.perf_counter
) -> RunRecord:
    """Run one case once.

    Args:
        graph: The configuration's graph.
        case: The case to run.
        config: The configuration's name.
        run: Which run of the case this is, counting from 1.
        clock: Returns the current time in seconds. Tests pass a fake one.

    Returns:
        What the run produced. A devassist error, such as a model timeout,
        becomes a failed run instead of stopping the eval.
    """
    start = clock()
    try:
        state = run_graph(graph, case.code, case.language)
    except DevassistError as error:
        logger.warning("%s %s run %d failed: %s", config, case.id, run, error)
        return RunRecord(
            config=config,
            case_id=case.id,
            run=run,
            latency_seconds=clock() - start,
            error_type=type(error).__name__,
            error=str(error),
        )
    return RunRecord(
        config=config,
        case_id=case.id,
        run=run,
        latency_seconds=clock() - start,
        input_error=state.error,
        result=state.result,
        warnings=state.warnings,
        metrics=state.metrics,
    )


def run_eval(
    cases: Sequence[EvalCase],
    graphs: Mapping[str, Graph],
    runs: int,
    *,
    workers: int = DEFAULT_WORKERS,
    clock: Clock = time.perf_counter,
    on_record: Callable[[RunRecord], None] | None = None,
) -> list[RunRecord]:
    """Run every case ``runs`` times under each configuration, several at once.

    Runs are queued run by run and case by case, alternating configurations,
    so a slow patch of the API slows every configuration alike.

    Args:
        cases: The cases to run.
        graphs: Each configuration's graph, by name, in the order to report them.
        runs: Runs of each case under each configuration.
        workers: The most runs in progress at once.
        clock: Returns the current time in seconds. Tests pass a fake one.
        on_record: Called with each record as soon as its run finishes.

    Returns:
        Every record, sorted by configuration, case and run.

    Raises:
        Exception: Any error that isn't a devassist error, such as a bug. Runs
            still queued are cancelled, so no more paid calls start.
    """
    tasks = [(name, case, run) for run in range(1, runs + 1) for case in cases for name in graphs]
    records = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(run_case, graphs[name], case, name, run, clock=clock)
            for name, case, run in tasks
        ]
        try:
            for future in as_completed(futures):
                record = future.result()
                records.append(record)
                if on_record is not None:
                    on_record(record)
        except BaseException:
            pool.shutdown(cancel_futures=True)  # a bug or Ctrl+C: start no more paid runs
            raise
    config_order = {name: index for index, name in enumerate(graphs)}
    case_order = {case.id: index for index, case in enumerate(cases)}
    return sorted(records, key=lambda r: (config_order[r.config], case_order[r.case_id], r.run))


@dataclass(frozen=True)
class CostEstimate:
    """The estimated cost of one configuration's runs.

    Attributes:
        typical_usd: If every run passes verification in its first round.
        worst_usd: If every run uses every round, so the design always escalates.
    """

    typical_usd: float
    worst_usd: float


def _tokens(text: str) -> int:
    """Estimate a text's tokens from its length."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _call_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price one model call at list price."""
    price = price_for(model)
    if price is None:
        raise ValueError(f"{model} has no price in devassist.config.PRICES.")
    dollars = input_tokens * price.input_per_million + output_tokens * price.output_per_million
    return dollars / TOKENS_PER_MILLION


def estimate_cost(
    cases: Sequence[EvalCase], configuration: Configuration, runs: int, base_settings: Settings
) -> CostEstimate:
    """Estimate one configuration's cost from the prompts' sizes, without calling the API.

    Args:
        cases: The cases to run.
        configuration: The configuration.
        runs: Runs of each case.
        base_settings: The loaded settings.

    Returns:
        The typical and worst-case cost. Inputs devassist rejects cost nothing.
    """
    settings = settings_for(configuration, base_settings)
    fixed_input = _tokens(system_prompt()) + _tokens(json.dumps(output_schema(Analysis)))
    typical = worst = 0.0
    for case in cases:
        if not reaches_model(case):
            continue
        code = _tokens(case.code)
        draft_input = fixed_input + _tokens(draft_prompt(case.code, []))
        output = OUTPUT_TOKENS_PER_CODE_TOKEN * code + OUTPUT_TOKENS_FIXED
        first = _call_cost(model_for_round(1, settings), draft_input, output)
        # A later round also sends back the previous rewrite, about the code's size.
        later = math.fsum(
            _call_cost(model_for_round(round_number, settings), draft_input + code, output)
            for round_number in range(2, settings.max_rounds + 1)
        )
        typical += first
        worst += first + later
    return CostEstimate(typical_usd=typical * runs, worst_usd=worst * runs)


def render_estimate(
    estimates: Mapping[str, CostEstimate], cases: Sequence[EvalCase], runs: int
) -> str:
    """Render the cost estimates as a plain-text table."""
    reaching = sum(reaches_model(case) for case in cases)
    rows = [[name, f"${e.typical_usd:.3f}", f"${e.worst_usd:.3f}"] for name, e in estimates.items()]
    typical = math.fsum(e.typical_usd for e in estimates.values())
    worst = math.fsum(e.worst_usd for e in estimates.values())
    rows.append(["total", f"${typical:.3f}", f"${worst:.3f}"])
    return "\n".join(
        [
            f"Estimated cost: {len(cases)} cases ({reaching} reach the model) x {runs} runs",
            "",
            *format_table(["config", "typical", "worst"], rows),
            "",
            "typical: every run passes in its first round. worst: every run uses every round.",
            f"Token counts are rough (about {CHARS_PER_TOKEN} characters per token); "
            "the smoke run measures real ones.",
        ]
    )


def describe(record: RunRecord) -> str:
    """Summarize a finished run in one line, for the progress log."""
    head = f"{record.config} {record.case_id} run {record.run}"
    seconds = f"{record.latency_seconds:.1f} s"
    if record.error is not None or record.metrics is None:
        return f"{head}: failed ({record.error_type}) after {seconds}"
    if record.input_error is not None:
        return f"{head}: input rejected in {seconds}"
    cost = "cost unknown" if record.metrics.cost_usd is None else f"${record.metrics.cost_usd:.5f}"
    return f"{head}: {record.metrics.rounds} round(s), {cost}, {seconds}"


def write_csv(path: Path, rows: Sequence[CsvRow]) -> None:
    """Write the per-case rows, with LF line endings like every other file in the repo."""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _positive_int(text: str) -> int:
    """Parse a whole number of 1 or more, for argparse."""
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a whole number") from error
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, not {value}")
    return value


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.run_evals",
        description="Run devassist's eval and judge the design against D8's bar.",
    )
    parser.add_argument("--smoke", action="store_true", help="run only the 12 smoke cases")
    parser.add_argument(
        "--runs",
        type=_positive_int,
        default=DEFAULT_RUNS,
        help=f"runs of each case under each configuration (default {DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        choices=list(CONFIGURATIONS),
        default=list(CONFIGURATIONS),
        help="the configurations to run (default: all three)",
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=DEFAULT_WORKERS,
        help=f"the most runs in progress at once (default {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="estimate the cost without calling the API"
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    make_llm: LLMFactory = _anthropic_client,
    make_analyzers: AnalyzerFactory = default_analyzers,
    load_settings: Callable[[], Settings] = Settings,
    results_dir: Path = RESULTS_DIR,
    environ: Mapping[str, str] = os.environ,
) -> int:
    """Run the eval from the command line.

    Args:
        argv: The arguments; ``None`` reads them from the command line.
        make_llm: Creates a model client from a configuration's settings.
        make_analyzers: Creates the static analyzers.
        load_settings: Loads the settings, which supply the API key.
        results_dir: Where the result files go.
        environ: The environment variables, checked for LangSmith tracing.

    Returns:
        The exit code: 0 on success, 1 if the eval couldn't start.
    """
    args = parse_args(argv)
    cases = [case for case in load_cases() if case.smoke or not args.smoke]
    configurations = [CONFIGURATIONS[name] for name in dict.fromkeys(args.configs)]
    base_settings = load_settings()
    if args.dry_run:
        estimates = {
            c.name: estimate_cost(cases, c, args.runs, base_settings) for c in configurations
        }
        print(render_estimate(estimates, cases, args.runs))
        return 0

    try:
        graphs = build_graphs(
            configurations, base_settings, make_llm=make_llm, make_analyzers=make_analyzers
        )
    except DevassistError as error:
        logger.error("Error: %s", error)
        return ERROR_EXIT_CODE
    if langsmith_status(environ) is LangSmithStatus.ON:
        logger.warning(
            "LangSmith tracing is on: every snippet and answer in this eval is sent to LangSmith."
        )

    label = "smoke" if args.smoke else "full"
    results_dir.mkdir(parents=True, exist_ok=True)
    total = len(cases) * args.runs * len(configurations)
    runs_path = results_dir / f"{label}-runs.jsonl"
    with runs_path.open("w", encoding="utf-8", newline="\n") as runs_file:
        finished = 0

        def save(record: RunRecord) -> None:
            nonlocal finished
            finished += 1
            runs_file.write(record.model_dump_json() + "\n")
            runs_file.flush()
            logger.info("[%d/%d] %s", finished, total, describe(record))

        records = run_eval(cases, graphs, args.runs, workers=args.workers, on_record=save)

    summary = build_summary(
        cases,
        records,
        configurations,
        label=label,
        runs_per_case=args.runs,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    (results_dir / f"{label}-summary.json").write_text(
        summary.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    write_csv(results_dir / f"{label}-cases.csv", case_rows(cases, records, configurations))
    print(render_report(summary))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # The HTTP client logs every request at INFO, which buries the progress lines.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    raise SystemExit(main())
