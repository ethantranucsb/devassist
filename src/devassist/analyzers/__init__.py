"""Static analyzers: the protocol, and the syntax, ruff and bandit checks."""

from devassist.analyzers.bandit_analyzer import BanditAnalyzer
from devassist.analyzers.base import Analyzer
from devassist.analyzers.ruff_analyzer import RuffAnalyzer
from devassist.analyzers.syntax_analyzer import SyntaxAnalyzer


def default_analyzers() -> list[Analyzer]:
    """Return the analyzers devassist runs by default, syntax first."""
    return [SyntaxAnalyzer(), RuffAnalyzer(), BanditAnalyzer()]


__all__ = [
    "Analyzer",
    "BanditAnalyzer",
    "RuffAnalyzer",
    "SyntaxAnalyzer",
    "default_analyzers",
]
