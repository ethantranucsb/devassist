"""Checks that a suggested rewrite is safe to show the user.

A rewrite passes when it parses, adds no new static-analysis findings and
keeps the original's public functions, classes and methods with the same
parameters. Everything here reads code with ``ast``; nothing is executed.
"""

import ast
from collections import Counter
from collections.abc import Sequence

from devassist.analyzers import SyntaxAnalyzer
from devassist.models import Finding

CLASS_SIGNATURE = "class"


def _is_public(name: str) -> bool:
    """Treat names without a leading underscore, and dunder methods such as __init__, as public."""
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _parameters(args: ast.arguments) -> str:
    """Render a parameter list by name and kind only, such as ``(a, /, b, *args, c, **kwargs)``."""
    parts = [arg.arg for arg in args.posonlyargs]
    if args.posonlyargs:
        parts.append("/")
    parts += [arg.arg for arg in args.args]
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")
    parts += [arg.arg for arg in args.kwonlyargs]
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return f"({', '.join(parts)})"


def public_signatures(code: str) -> dict[str, str] | None:
    """List the public top-level functions and classes, and the public methods of those classes.

    Type hints and default values are left out on purpose: adding hints is an
    improvement, while removing or renaming a parameter breaks callers.

    Args:
        code: The Python source to read.

    Returns:
        A map from name (``"run"``, ``"Runner"``, ``"Runner.go"``) to its
        parameter list, or ``"class"`` for a class. ``None`` if the code
        doesn't parse, including code nested too deeply for the parser.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, RecursionError):
        return None
    signatures: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_public(node.name):
            signatures[node.name] = _parameters(node.args)
        elif isinstance(node, ast.ClassDef) and _is_public(node.name):
            signatures[node.name] = CLASS_SIGNATURE
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and _is_public(
                    item.name
                ):
                    signatures[f"{node.name}.{item.name}"] = _parameters(item.args)
    return signatures


def _syntax_problems(improved_findings: Sequence[Finding]) -> list[str]:
    """Report every syntax error in the rewrite."""
    return [
        f"syntax error on line {f.line}: {f.message}"
        for f in improved_findings
        if f.tool == SyntaxAnalyzer.name
    ]


def _new_finding_problems(
    original_findings: Sequence[Finding], improved_findings: Sequence[Finding]
) -> list[str]:
    """Report rules that fire more often in the rewrite than in the original.

    Findings are counted by tool and rule code rather than matched by line,
    because line numbers shift whenever code changes.
    """
    before = Counter((f.tool, f.code) for f in original_findings if f.tool != SyntaxAnalyzer.name)
    after: dict[tuple[str, str], list[Finding]] = {}
    for f in improved_findings:
        if f.tool != SyntaxAnalyzer.name:
            after.setdefault((f.tool, f.code), []).append(f)
    problems = []
    for (tool, code), found in after.items():
        extra = len(found) - before[(tool, code)]
        if extra > 0:
            example = found[0]
            problems.append(
                f"new {tool} finding {code} ({extra} more than the original), "
                f"for example on line {example.line}: {example.message}"
            )
    return problems


def _signature_problems(original_code: str, improved_code: str) -> list[str]:
    """Report public functions, classes and methods that disappeared or changed parameters."""
    before = public_signatures(original_code)
    after = public_signatures(improved_code)
    if before is None or after is None:
        return []
    problems = []
    for name, signature in before.items():
        if signature == CLASS_SIGNATURE:
            kind = "class"
        elif "." in name:
            kind = "method"
        else:
            kind = "function"
        if name not in after:
            problems.append(f"public {kind} `{name}` is missing")
        elif after[name] != signature:
            problems.append(f"public {kind} `{name}` changed from {signature} to {after[name]}")
    return problems


def verification_problems(
    original_code: str,
    original_findings: Sequence[Finding],
    improved_code: str,
    improved_findings: Sequence[Finding],
) -> list[str]:
    """Explain everything wrong with a rewrite, in plain English.

    Args:
        original_code: The submitted snippet.
        original_findings: What the analyzers reported for the snippet.
        improved_code: The model's suggested rewrite.
        improved_findings: What the analyzers reported for the rewrite.

    Returns:
        One sentence per problem, ready to feed back to the model. An empty
        list means the rewrite passed.
    """
    return (
        _syntax_problems(improved_findings)
        + _new_finding_problems(original_findings, improved_findings)
        + _signature_problems(original_code, improved_code)
    )
