"""Versioned prompt templates, loaded from the files in this package.

Each template is a file named ``<name>.<PROMPT_VERSION>.md``, so a new prompt
version can sit beside the old one and the eval can compare them.
Placeholders use ``string.Template`` (``$name``), which inserts values
literally, so braces and dollar signs in submitted code are never
interpreted. Text placed inside a tagged block has that block's closing tag
escaped, so a snippet can't end its block early and pose as instructions.
"""

import re
from collections.abc import Sequence
from functools import cache
from importlib.resources import files
from string import Template

from devassist.models import Analysis, Finding

PROMPT_VERSION = "v1"
NO_FINDINGS = "none"


@cache
def _read(name: str) -> str:
    """Return the text of one template file for the current prompt version."""
    return files(__name__).joinpath(f"{name}.{PROMPT_VERSION}.md").read_text(encoding="utf-8")


def _escape_closing_tag(text: str, tag: str) -> str:
    """Neutralize any ``</tag>`` in text that will be placed inside ``<tag>`` ... ``</tag>``."""
    return re.sub(rf"</\s*{tag}\s*>", f"&lt;/{tag}&gt;", text, flags=re.IGNORECASE)


def _number_lines(code: str) -> str:
    """Prefix each line with its number and a bar, such as ``4 | ``."""
    lines = code.splitlines() or [""]
    width = len(str(len(lines)))
    return "\n".join(f"{number:>{width}} | {line}" for number, line in enumerate(lines, start=1))


def _code_block(code: str, tag: str) -> str:
    """Prepare code for a tagged block: escape the closing tag, then number the lines."""
    return _number_lines(_escape_closing_tag(code, tag))


def _format_findings(findings: Sequence[Finding]) -> str:
    """List findings one per line, or say there are none."""
    if not findings:
        return NO_FINDINGS
    lines = [f"- line {f.line}: {f.tool} {f.code}: {f.message}" for f in findings]
    return _escape_closing_tag("\n".join(lines), "findings")


def system_prompt() -> str:
    """Return the system prompt: the reviewer's role and the rules for its answer."""
    return _read("system")


def draft_prompt(snippet: str, findings: Sequence[Finding]) -> str:
    """Return the first-round prompt for a snippet.

    Args:
        snippet: The submitted Python code.
        findings: What the static analyzers reported for the snippet.

    Returns:
        The user prompt, with the numbered snippet and findings in their tags.
    """
    return Template(_read("draft")).substitute(
        snippet=_code_block(snippet, "snippet"),
        findings=_format_findings(findings),
    )


def refine_prompt(
    snippet: str,
    findings: Sequence[Finding],
    previous: Analysis,
    problems: Sequence[str],
) -> str:
    """Return the prompt for a later round, after verification failed.

    Args:
        snippet: The submitted Python code.
        findings: What the static analyzers reported for the snippet.
        previous: The previous answer, whose improved code failed verification.
        problems: What verification found wrong with the previous improved code.

    Returns:
        The user prompt, with the snippet, findings, previous code and problems
        each in their own tags.
    """
    problem_lines = "\n".join(f"- {problem}" for problem in problems)
    return Template(_read("refine")).substitute(
        snippet=_code_block(snippet, "snippet"),
        findings=_format_findings(findings),
        previous_code=_code_block(previous.improved_code, "previous_code"),
        problems=_escape_closing_tag(problem_lines, "problems"),
    )


def retry_prompt(errors: Sequence[str]) -> str:
    """Return the follow-up prompt sent when an answer fails validation.

    Args:
        errors: One line per validation error, such as ``summary: must not be empty``.

    Returns:
        The user prompt, with the errors listed in their tags.
    """
    error_lines = "\n".join(f"- {error}" for error in errors)
    return Template(_read("retry")).substitute(errors=_escape_closing_tag(error_lines, "errors"))
