import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from devassist.models import Analysis, Category, Issue, RunMetrics, Severity
from evals.cases import Difficulty, EvalCase, ExpectedIssue, Kind
from evals.chart import ChartPoint, chart_points, log_position, main, render_svg
from evals.configurations import BASELINE, CONFIGURATIONS, DESIGN
from evals.scoring import RunRecord

SVG = "{http://www.w3.org/2000/svg}"
POINTS = [
    ChartPoint("Design", 0.0009, 0.926, (0.83, 1.0), highlight=True),
    ChartPoint("Haiku alone", 0.00089, 0.889, (0.78, 0.98)),
    ChartPoint("Sonnet alone", 0.0118, 0.944, (0.86, 1.0)),
]


def planted(case_id: str) -> EvalCase:
    """Return a planted case whose expected bug is on line 1."""
    return EvalCase(
        id=case_id,
        kind=Kind.PLANTED,
        difficulty=Difficulty.EASY,
        snippet="a = 1\n",
        expected_issues=[
            ExpectedIssue(category=Category.BUG, line_start=1, line_end=1, note="Wrong.")
        ],
    )


CASES = [planted("bug-one"), planted("bug-two")]


def review(config: str, case_id: str, *, found: bool, cost: float) -> RunRecord:
    """Return a run record that does or doesn't report the expected bug."""
    issues = [
        Issue(
            severity=Severity.HIGH,
            category=Category.BUG,
            line_start=1,
            line_end=1,
            description="Wrong.",
        )
    ]
    return RunRecord(
        config=config,
        case_id=case_id,
        run=1,
        latency_seconds=1.0,
        result=Analysis(
            summary="Sets a.",
            documentation="Sets a.",
            issues=issues if found else [],
            improved_code="a = 1\n",
        ),
        metrics=RunMetrics(
            rounds=1,
            models=["claude-haiku-5-5"],
            escalations=0,
            retries=0,
            input_tokens=100,
            output_tokens=50,
            cost_usd=cost,
            unpriced_models=[],
            latency_seconds=1.0,
        ),
    )


def parse(svg: str) -> ET.Element:
    """Parse the chart, failing the test if it isn't well-formed XML."""
    return ET.fromstring(svg)


def test_the_chart_is_well_formed_svg_with_a_point_and_label_per_configuration() -> None:
    root = parse(render_svg(POINTS))

    texts = [element.text for element in root.iter(f"{SVG}text")]
    assert root.tag == f"{SVG}svg"
    assert len(list(root.iter(f"{SVG}circle"))) == 3
    for point in POINTS:
        assert any(point.label in (text or "") for text in texts)


def test_each_point_has_a_whisker_for_its_interval() -> None:
    root = parse(render_svg(POINTS))

    whiskers = [line for line in root.iter(f"{SVG}line") if line.get("class") == "whisker"]
    assert len(whiskers) == 3


def test_the_cost_axis_is_logarithmic() -> None:
    domain = (math.log10(0.0005), math.log10(0.05))

    one_decade = log_position(0.01, domain, 400) - log_position(0.001, domain, 400)
    next_decade = log_position(0.001, domain, 400) - log_position(0.0001, domain, 400)

    assert one_decade == pytest.approx(next_decade)
    assert log_position(0.0005, domain, 400) == pytest.approx(0.0)
    assert log_position(0.05, domain, 400) == pytest.approx(400.0)


def test_the_highlighted_point_stands_out() -> None:
    svg = render_svg(POINTS)

    assert svg.count('class="point highlight"') == 1


def test_labels_are_escaped() -> None:
    point = ChartPoint("A & B <test>", 0.001, 0.9, (0.8, 1.0))

    assert "A &amp; B &lt;test&gt;" in render_svg([point])


def test_chart_points_come_from_the_runs_with_case_level_recall() -> None:
    records = [
        review(DESIGN, "bug-one", found=True, cost=0.001),
        review(DESIGN, "bug-two", found=False, cost=0.001),
        review(BASELINE, "bug-one", found=True, cost=0.01),
        review(BASELINE, "bug-two", found=True, cost=0.01),
    ]

    points = chart_points(
        CASES, records, [CONFIGURATIONS[DESIGN], CONFIGURATIONS[BASELINE]], resamples=200
    )

    design, sonnet = points
    assert (design.label, design.recall, design.cost_usd) == ("Design", 0.5, 0.001)
    assert design.highlight
    assert design.recall_interval == (0.0, 1.0)
    assert (sonnet.label, sonnet.recall, sonnet.recall_interval) == (
        "Sonnet alone",
        1.0,
        (1.0, 1.0),
    )
    assert not sonnet.highlight


def test_main_writes_the_chart_from_saved_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    records = [
        review(name, case.id, found=True, cost=0.001 if name != BASELINE else 0.01)
        for name in CONFIGURATIONS
        for case in CASES
    ]
    runs.write_text("".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8")
    out = tmp_path / "chart.svg"

    assert main(runs_path=runs, out_path=out, cases=CASES) == 0

    assert b"\r" not in out.read_bytes()
    parse(out.read_text(encoding="utf-8"))
