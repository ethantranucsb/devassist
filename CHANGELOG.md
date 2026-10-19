# Changelog

All notable changes to devassist. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-19

The first release.

### Added

- `devassist explain` reviews a Python snippet from a file or stdin. It prints a summary, documentation, the issues found and improved code, as Markdown or JSON.
- A Markdown review of a file is also saved next to it, as `NAME.review.md`, to read in an editor's Markdown preview.
- A LangGraph workflow checks every rewrite with `ast`, ruff and bandit and sends the problems back to the model, for at most 3 rounds. Claude Haiku 5.5 answers rounds 1 and 2; Claude Sonnet 5.5 answers round 3.
- A second review asks a different model, Claude Sonnet 5.5, for issues the first review missed, and any it finds are added and fixed in a verified round. A second eval found it caught every planted issue but missed the bar on cost, latency and false alarms. It's on by default anyway, as a choice of recall over cost; `--no-review` skips it. A third eval, with Sonnet making the fix, found that version cost as much as Sonnet alone, so it wasn't kept.
- The model's answer is structured output validated with Pydantic, retried once with the errors fed back.
- The analyzers ignore `# noqa` and `# nosec` comments, so a rewrite can't silence the checks that verify it.
- Input checks reject empty, oversized, non-Python and too deeply nested snippets. Each kind of failure has its own exit code.
- Every run reports its rounds, escalations, retries, tokens, cost at list prices and latency.
- `--trace` streams each graph step. Opt-in LangSmith tracing prints a notice whenever snippets leave the machine.
- An eval of 30 labeled cases compares the design with Haiku alone and Sonnet alone, judged against a bar set before any results existed. The results are in `evals/results/`.
- A Docker image and a pip install path, both checked in CI.
