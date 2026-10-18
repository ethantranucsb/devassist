import itertools
import json
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from devassist.agent import Graph
from devassist.analyzers import Analyzer
from devassist.config import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    ESCALATION_ROUND,
    MAX_ROUNDS,
    MAX_SNIPPET_CHARS,
    Settings,
)
from devassist.exceptions import LLMError, MissingAPIKeyError
from devassist.llm import FakeLLMClient, LLMClient
from devassist.models import Analysis
from evals.cases import Behavior, EvalCase, Kind, load_cases
from evals.configurations import BASELINE, CONFIGURATIONS, DESIGN, settings_for
from evals.run_evals import CostEstimate, build_graphs, estimate_cost, main, run_case, run_eval
from evals.scoring import RunRecord

ANSWER = Analysis(
    summary="Sets x.", documentation="Sets x to 1.", issues=[], improved_code="x = 1\n"
)
CASE = EvalCase(id="clean-case", kind=Kind.CLEAN, snippet="x = 1\n")
OTHER = EvalCase(id="clean-other", kind=Kind.CLEAN, snippet="y = 2\n")
REJECTED = EvalCase(
    id="adversarial-js",
    kind=Kind.ADVERSARIAL,
    behavior=Behavior.INPUT_ERROR,
    language="javascript",
    snippet="var x;\n",
)
ALL_CASES = load_cases()


def no_analyzers() -> Sequence[Analyzer]:
    """Skip ruff and bandit, so tests run fast. Verify still checks signatures."""
    return []


def base_settings() -> Settings:
    """Return default settings without reading the environment or .env."""
    return Settings.model_construct()


def answering(client: LLMClient) -> Callable[[Settings], LLMClient]:
    """Return a client factory that hands out this client for every configuration."""
    return lambda _settings: client


def graphs_for(client: LLMClient, *names: str) -> dict[str, Graph]:
    """Build a graph for each named configuration, all answered by one client."""
    return build_graphs(
        [CONFIGURATIONS[name] for name in names],
        base_settings(),
        make_llm=answering(client),
        make_analyzers=no_analyzers,
    )


def ticking_clock() -> Callable[[], float]:
    """Return a clock that moves forward one second each time it's read."""
    ticks = itertools.count()
    return lambda: float(next(ticks))


# Configurations


def test_the_configurations_use_the_shipped_model_names() -> None:
    def models(name: str) -> tuple[str, str]:
        return CONFIGURATIONS[name].model, CONFIGURATIONS[name].fallback_model

    assert models(DESIGN) == (DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL)
    assert models("haiku") == (DEFAULT_MODEL, DEFAULT_MODEL)
    assert models(BASELINE) == (DEFAULT_FALLBACK_MODEL, DEFAULT_FALLBACK_MODEL)


def test_settings_for_resets_the_loop_settings_to_their_shipped_values() -> None:
    local = Settings.model_construct(max_rounds=1, escalation_round=2, max_snippet_chars=10)

    settings = settings_for(CONFIGURATIONS["haiku"], local)

    assert (settings.model, settings.fallback_model) == (DEFAULT_MODEL, DEFAULT_MODEL)
    assert (settings.max_rounds, settings.escalation_round, settings.max_snippet_chars) == (
        MAX_ROUNDS,
        ESCALATION_ROUND,
        MAX_SNIPPET_CHARS,
    )


# Running one case


def test_run_case_records_the_answer_metrics_and_latency() -> None:
    graph = graphs_for(FakeLLMClient([ANSWER]), DESIGN)[DESIGN]

    record = run_case(graph, CASE, DESIGN, 2, clock=ticking_clock())

    assert (record.config, record.case_id, record.run) == (DESIGN, CASE.id, 2)
    assert record.result == ANSWER
    assert record.metrics is not None and record.metrics.rounds == 1
    assert record.error is None
    assert record.latency_seconds == 1.0


def test_a_model_error_becomes_a_failed_run_instead_of_stopping_the_eval() -> None:
    graph = graphs_for(FakeLLMClient([LLMError("The model call timed out.")]), DESIGN)[DESIGN]

    record = run_case(graph, CASE, DESIGN, 1)

    assert record.error_type == "LLMError"
    assert record.error == "The model call timed out."
    assert record.result is None
    assert record.metrics is None


def test_a_rejected_input_is_recorded_without_any_model_call() -> None:
    client = FakeLLMClient([])

    record = run_case(graphs_for(client, DESIGN)[DESIGN], REJECTED, DESIGN, 1)

    assert record.input_error is not None
    assert record.metrics is not None and record.metrics.rounds == 0
    assert client.requests == []


# Running the eval


def test_run_eval_runs_every_case_under_every_configuration() -> None:
    client = FakeLLMClient([ANSWER] * 20)
    finished: list[RunRecord] = []

    records = run_eval(
        [CASE, OTHER], graphs_for(client, DESIGN, BASELINE), 3, workers=4, on_record=finished.append
    )

    assert [(r.config, r.case_id, r.run) for r in records] == [
        (config, case, run)
        for config in (DESIGN, BASELINE)
        for case in (CASE.id, OTHER.id)
        for run in (1, 2, 3)
    ]
    assert len(finished) == 12
    assert {request.model for request in client.requests} == {
        DEFAULT_MODEL,
        DEFAULT_FALLBACK_MODEL,
    }


def test_runs_alternate_between_configurations_so_a_slow_api_affects_all_alike() -> None:
    finished: list[RunRecord] = []

    run_eval(
        [CASE, OTHER],
        graphs_for(FakeLLMClient([ANSWER] * 8), DESIGN, BASELINE),
        2,
        workers=1,
        on_record=finished.append,
    )

    assert [(r.run, r.case_id, r.config) for r in finished[:4]] == [
        (1, CASE.id, DESIGN),
        (1, CASE.id, BASELINE),
        (1, OTHER.id, DESIGN),
        (1, OTHER.id, BASELINE),
    ]


def test_an_unexpected_error_stops_the_eval() -> None:
    client = FakeLLMClient([RuntimeError("a bug in devassist"), *[ANSWER] * 5])

    with pytest.raises(RuntimeError, match="a bug in devassist"):
        run_eval([CASE], graphs_for(client, DESIGN), 3, workers=1)


# The cost estimate


def test_inputs_devassist_rejects_cost_nothing() -> None:
    estimate = estimate_cost([REJECTED], CONFIGURATIONS[BASELINE], 3, base_settings())

    assert estimate == CostEstimate(typical_usd=0.0, worst_usd=0.0)


def test_the_design_starts_on_haiku_and_its_worst_case_lies_between_the_others() -> None:
    def estimate(name: str) -> CostEstimate:
        return estimate_cost(ALL_CASES, CONFIGURATIONS[name], 3, base_settings())

    design, haiku, sonnet = estimate(DESIGN), estimate("haiku"), estimate(BASELINE)

    assert design.typical_usd == pytest.approx(haiku.typical_usd)
    assert haiku.worst_usd < design.worst_usd < sonnet.worst_usd
    assert all(e.typical_usd < e.worst_usd for e in (design, haiku, sonnet))


def test_the_estimate_grows_in_proportion_to_the_runs() -> None:
    one = estimate_cost(ALL_CASES, CONFIGURATIONS[DESIGN], 1, base_settings())
    three = estimate_cost(ALL_CASES, CONFIGURATIONS[DESIGN], 3, base_settings())

    assert three.typical_usd == pytest.approx(3 * one.typical_usd)
    assert three.worst_usd == pytest.approx(3 * one.worst_usd)


# The command line


def no_client(_settings: Settings) -> LLMClient:
    """Fail the test if a model client is created."""
    raise AssertionError("no model client should be created")


def test_the_dry_run_prints_an_estimate_without_calling_the_api(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--dry-run"], make_llm=no_client, load_settings=base_settings, results_dir=tmp_path
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "30 cases" in output
    assert all(name in output for name in CONFIGURATIONS)
    assert "$" in output
    assert list(tmp_path.iterdir()) == []


def test_a_smoke_run_writes_raw_records_a_summary_and_a_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeLLMClient([ANSWER] * 200)

    code = main(
        ["--smoke", "--runs", "1", "--configs", DESIGN, BASELINE],
        make_llm=answering(client),
        make_analyzers=no_analyzers,
        load_settings=base_settings,
        results_dir=tmp_path,
    )

    assert code == 0
    raw = (tmp_path / "smoke-runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 12 * 2
    assert RunRecord.model_validate_json(raw[0]).config in (DESIGN, BASELINE)
    summary = json.loads((tmp_path / "smoke-summary.json").read_text(encoding="utf-8"))
    assert summary["label"] == "smoke"
    assert list(summary["configs"]) == [DESIGN, BASELINE]
    assert len(summary["criteria"]) == 6
    csv_lines = (tmp_path / "smoke-cases.csv").read_bytes().split(b"\n")
    assert csv_lines[0].startswith(b"config,case_id,")
    assert b"\r" not in csv_lines[0]
    assert len([line for line in csv_lines if line]) == 1 + 12 * 2
    assert "D8" in capsys.readouterr().out


def test_a_missing_api_key_stops_before_any_run_and_keeps_earlier_results(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    earlier = tmp_path / "smoke-runs.jsonl"
    earlier.write_text("earlier results\n", encoding="utf-8")

    def missing_key(_settings: Settings) -> LLMClient:
        raise MissingAPIKeyError("ANTHROPIC_API_KEY is not set.")

    code = main(
        ["--smoke"], make_llm=missing_key, load_settings=base_settings, results_dir=tmp_path
    )

    assert code == 1
    assert "ANTHROPIC_API_KEY is not set." in caplog.text
    assert earlier.read_text(encoding="utf-8") == "earlier results\n"


def test_the_runner_warns_when_langsmith_tracing_is_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    main(
        ["--smoke", "--runs", "1", "--configs", DESIGN],
        make_llm=answering(FakeLLMClient([ANSWER] * 100)),
        make_analyzers=no_analyzers,
        load_settings=base_settings,
        results_dir=tmp_path,
        environ={"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "placeholder"},
    )

    assert "LangSmith tracing is on" in caplog.text


def test_runs_must_be_a_positive_number(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--runs", "0", "--dry-run"], make_llm=no_client, load_settings=base_settings)

    assert "must be 1 or more" in capsys.readouterr().err
