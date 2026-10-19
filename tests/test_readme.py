from pathlib import Path

from devassist.agent import build_graph, mermaid_diagram
from devassist.config import Settings
from devassist.llm import FakeLLMClient

README = Path(__file__).parents[1] / "README.md"


def test_the_readme_diagram_matches_the_compiled_graph() -> None:
    graph = build_graph(FakeLLMClient([]), [], Settings.model_construct())
    block = f"```mermaid\n{mermaid_diagram(graph)}```"

    assert block in README.read_text(encoding="utf-8"), (
        f"The README's diagram is out of date. Replace its mermaid block with:\n\n{block}"
    )


def test_the_diagram_shows_every_node_and_the_conditional_routes() -> None:
    diagram = mermaid_diagram(build_graph(FakeLLMClient([]), [], Settings.model_construct()))

    nodes = (
        "validate_input",
        "run_static_checks",
        "draft",
        "verify",
        "review",
        "refine",
        "finalize",
    )
    for node in nodes:
        assert f"\t{node}(" in diagram
    assert "verify -.-> refine" in diagram
    assert "verify -.-> review" in diagram
    assert "review -.-> refine" in diagram
    assert "validate_input -.-> __end__" in diagram
