"""The eval write-up's cost-against-recall chart, drawn as SVG from the saved eval runs.

Standard library only. Regenerate it after a new full eval with:

    uv run python -m evals.chart
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from evals.cases import EvalCase, Kind, load_cases
from evals.configurations import CONFIGURATIONS, DESIGN, Configuration
from evals.scoring import RunRecord
from evals.stats import bootstrap_interval, mean
from evals.summary import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CONFIDENCE,
    case_recall,
    score_records,
    summarize_config,
)

RUNS_PATH = Path(__file__).with_name("results") / "full-runs.jsonl"
CHART_PATH = Path(__file__).parents[1] / "docs" / "images" / "cost-vs-recall.svg"
LABELS = {"design": "Design", "haiku": "Haiku alone", "sonnet": "Sonnet alone"}

WIDTH, HEIGHT = 680, 420
LEFT, RIGHT, TOP, BOTTOM = 72, 100, 56, 64  # margins around the plot, in pixels
PLOT_WIDTH, PLOT_HEIGHT = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
COST_PADDING = 2.0  # the cost axis runs from half the cheapest to twice the dearest
TICK_STEPS = (1, 2, 5)  # cost ticks at 1, 2 and 5 times each power of ten
NUDGE = 5  # pixels apart for points that would sit on top of each other
LABEL_WIDTH, LABEL_HEIGHT = 120, 32  # room for a two-line label beside its point
HIGHLIGHT, MUTED, INK, GRID = "#6d28d9", "#6b7280", "#111827", "#e5e7eb"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


@dataclass(frozen=True)
class ChartPoint:
    """One configuration on the chart.

    Attributes:
        label: Its name on the chart.
        cost_usd: Mean cost per snippet.
        recall: Mean recall over planted cases.
        recall_interval: The bootstrap interval for recall.
        highlight: Whether to draw it in the highlight color.
    """

    label: str
    cost_usd: float
    recall: float
    recall_interval: tuple[float, float]
    highlight: bool = False


def chart_points(
    cases: Sequence[EvalCase],
    records: Sequence[RunRecord],
    configurations: Sequence[Configuration],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> list[ChartPoint]:
    """Compute each configuration's cost, recall and recall interval from its runs.

    Args:
        cases: Every case, in dataset order.
        records: Every run's record.
        configurations: The configurations to plot, in order.
        resamples: Resamples behind each interval.

    Returns:
        One point per configuration, with recall averaged by case as in D8.

    Raises:
        ValueError: If a configuration has no planted cases or no known cost.
    """
    table = score_records(cases, records)
    points = []
    for configuration in configurations:
        scores = table[configuration.name]
        summary = summarize_config(configuration, cases, scores)
        if summary.recall is None or summary.mean_cost_usd is None:
            raise ValueError(f"{configuration.name} has no recall or cost to plot")
        recalls = [
            case_recall(scores[case.id])
            for case in cases
            if case.kind is Kind.PLANTED and case.id in scores
        ]

        def recall_of(indices: Sequence[int], recalls: list[float] = recalls) -> float:
            return mean([recalls[i] for i in indices])

        interval = bootstrap_interval(
            len(recalls), recall_of, resamples=resamples, seed=BOOTSTRAP_SEED, confidence=CONFIDENCE
        )
        points.append(
            ChartPoint(
                label=LABELS.get(configuration.name, configuration.name),
                cost_usd=summary.mean_cost_usd,
                recall=summary.recall,
                recall_interval=interval,
                highlight=configuration.name == DESIGN,
            )
        )
    return points


def log_position(value: float, domain: tuple[float, float], length: float) -> float:
    """Place a value on a log-scale axis.

    Args:
        value: The value, above zero.
        domain: The axis's ends, as base-10 logarithms.
        length: The axis's length in pixels.

    Returns:
        The distance from the axis's start, in pixels.
    """
    low, high = domain
    return (math.log10(value) - low) / (high - low) * length


def _cost_ticks(low: float, high: float) -> list[float]:
    """Return the 1, 2 and 5 times powers of ten that fall between low and high."""
    ticks = []
    for exponent in range(math.floor(math.log10(low)), math.ceil(math.log10(high)) + 1):
        for step in TICK_STEPS:
            value = step * 10.0**exponent
            if low <= value <= high:
                ticks.append(value)
    return ticks


def render_svg(points: Sequence[ChartPoint]) -> str:
    """Draw recall against cost per snippet, log scale, with interval whiskers.

    Args:
        points: The configurations to plot.

    Returns:
        A standalone SVG document with a white background, so it reads the
        same in light and dark themes.
    """
    costs = [point.cost_usd for point in points]
    low_cost, high_cost = min(costs) / COST_PADDING, max(costs) * COST_PADDING
    domain = (math.log10(low_cost), math.log10(high_cost))
    lowest = min(min(point.recall_interval[0], point.recall) for point in points)
    y_low_tenths = min(9, math.floor(lowest * 10))

    def x_of(cost: float) -> float:
        return LEFT + log_position(cost, domain, PLOT_WIDTH)

    def y_of(recall: float) -> float:
        return TOP + (1.0 - recall) / (1.0 - y_low_tenths / 10) * PLOT_HEIGHT

    xs = [x_of(point.cost_usd) for point in points]
    order = sorted(range(len(points)), key=lambda i: xs[i])
    for left, right in zip(order, order[1:], strict=False):
        if xs[right] - xs[left] < 2 * NUDGE:
            xs[left] -= NUDGE
            xs[right] += NUDGE

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{FONT}" font-size="13" fill="{INK}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
        f'<text x="{LEFT}" y="28" font-size="15" font-weight="600">'
        "Recall against cost per snippet</text>",
        f'<text x="{LEFT}" y="46" fill="{MUTED}">'
        f"Whiskers: {CONFIDENCE:.0%} bootstrap intervals over cases</text>",
    ]
    for tenth in range(y_low_tenths, 11):
        y = y_of(tenth / 10)
        parts.append(
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{LEFT + PLOT_WIDTH}" y2="{y:.1f}" '
            f'stroke="{GRID}"/>'
        )
        parts.append(f'<text x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{tenth * 10}%</text>')
    for tick in _cost_ticks(low_cost, high_cost):
        x = x_of(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{TOP}" x2="{x:.1f}" y2="{TOP + PLOT_HEIGHT}" stroke="{GRID}"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{TOP + PLOT_HEIGHT + 18}" text-anchor="middle">${tick:g}</text>'
        )
    parts += [
        f'<line x1="{LEFT}" y1="{TOP + PLOT_HEIGHT}" x2="{LEFT + PLOT_WIDTH}" '
        f'y2="{TOP + PLOT_HEIGHT}" stroke="{INK}"/>',
        f'<line x1="{LEFT}" y1="{TOP}" x2="{LEFT}" y2="{TOP + PLOT_HEIGHT}" stroke="{INK}"/>',
        f'<text x="{LEFT + PLOT_WIDTH / 2:.1f}" y="{HEIGHT - 18}" text-anchor="middle">'
        "Mean cost per snippet (log scale)</text>",
        f'<text transform="translate(20 {TOP + PLOT_HEIGHT / 2:.1f}) rotate(-90)" '
        'text-anchor="middle">Recall on planted issues</text>',
    ]
    placed: list[tuple[float, float]] = []  # labels drawn so far, by their top-left corner
    for i in sorted(range(len(points)), key=lambda i: y_of(points[i].recall)):
        point, x, y = points[i], xs[i], y_of(points[i].recall)
        color = HIGHLIGHT if point.highlight else MUTED
        low, high = (y_of(end) for end in point.recall_interval)
        parts += [
            f'<line class="whisker" x1="{x:.1f}" y1="{low:.1f}" x2="{x:.1f}" y2="{high:.1f}" '
            f'stroke="{color}" stroke-width="2"/>',
            f'<line class="cap" x1="{x - 4:.1f}" y1="{low:.1f}" x2="{x + 4:.1f}" y2="{low:.1f}" '
            f'stroke="{color}" stroke-width="2"/>',
            f'<line class="cap" x1="{x - 4:.1f}" y1="{high:.1f}" x2="{x + 4:.1f}" y2="{high:.1f}" '
            f'stroke="{color}" stroke-width="2"/>',
            f'<circle class="point{" highlight" if point.highlight else ""}" cx="{x:.1f}" '
            f'cy="{y:.1f}" r="6" fill="{color}"/>',
        ]
        cluster = [other for other in xs if abs(other - x) <= 3 * NUDGE]  # x included
        label_x, label_y = max(cluster) + 12, y  # clear every whisker beside this one
        for other_x, other_y in placed:  # move down past any label it would overlap
            if abs(other_x - label_x) < LABEL_WIDTH and abs(other_y - label_y) < LABEL_HEIGHT:
                label_y = other_y + LABEL_HEIGHT
        placed.append((label_x, label_y))
        parts += [
            f'<text x="{label_x:.1f}" y="{label_y + 4:.1f}" fill="{color}" font-weight="600">'
            f"{escape(point.label)}</text>",
            f'<text x="{label_x:.1f}" y="{label_y + 20:.1f}" fill="{MUTED}">'
            f"{point.recall:.1%}, ${point.cost_usd:.5f}</text>",
        ]
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main(
    *,
    runs_path: Path = RUNS_PATH,
    out_path: Path = CHART_PATH,
    cases: Sequence[EvalCase] | None = None,
) -> int:
    """Draw the chart from saved eval runs and write it to the docs folder.

    Args:
        runs_path: The raw runs JSONL a full eval wrote.
        out_path: Where to write the SVG.
        cases: The cases those runs used; ``None`` loads the dataset.

    Returns:
        The exit code, 0.
    """
    lines = runs_path.read_text(encoding="utf-8").splitlines()
    records = [RunRecord.model_validate_json(line) for line in lines if line.strip()]
    ran = {record.config for record in records}
    configurations = [c for c in CONFIGURATIONS.values() if c.name in ran]
    points = chart_points(load_cases() if cases is None else cases, records, configurations)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_svg(points), encoding="utf-8", newline="\n")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
