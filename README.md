# devassist

A command-line assistant that documents and reviews Python snippets, checking every suggestion with static analysis.

## Usage

Set up once: run `uv sync`, then copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`.

```
uv run devassist explain path/to/file.py                 # Markdown review
uv run devassist explain path/to/file.py --format json   # JSON, for scripts
cat path/to/file.py | uv run devassist explain           # read the snippet from stdin
uv run devassist --version
```

The review goes to stdout. Errors go to stderr, and so does a one-line run summary after a Markdown review: rounds, escalations, tokens, cost at list prices and time, such as `3 rounds (1 escalation) | 6,500 in / 3,000 out tokens | $0.0164 | 18.4 s`. JSON output carries the same numbers under `metrics`. A model missing from the price table in `config.py` shows its cost as unknown rather than wrong.

devassist writes UTF-8, so `> review.md` saves a clean UTF-8 review in most shells. Windows PowerShell 5.1 is the exception: it re-encodes text that passes through it, so `>` saves UTF-16 and can garble non-ASCII characters, and piped input loses them. On PowerShell 5.1, pass a file path rather than piping, and use PowerShell 7 when you need a saved review to be exact.

| Option | What it does |
|---|---|
| `--format markdown\|json` | How to print the review (default `markdown`). |
| `--model NAME` | Model for the rounds before escalation. Overrides `DEVASSIST_MODEL`. |
| `--max-rounds N` | Most model answers per run (default 3). Overrides `DEVASSIST_MAX_ROUNDS`. |
| `--language NAME` | The snippet's language. Only Python is supported. |
| `--verbose`, `-v` | Log escalations and retries to stderr. |
| `--trace` | Print each graph step to stderr as it finishes (see Tracing below). |

Settings come from command-line flags first, then environment variables, then `.env`, then the defaults in `.env.example`.

| Exit code | Meaning |
|---|---|
| 0 | Success, including a review with warnings. |
| 1 | An unexpected error (a bug). |
| 2 | A usage or input problem: missing, unreadable, non-UTF-8, empty or oversized input, or another language. |
| 3 | A configuration problem, such as a missing API key or an invalid setting. |
| 4 | A model or API problem: timeout, rate limit, refusal, or an answer that failed validation twice. |
| 5 | A static analyzer failed. |

## Tracing

`--trace` prints each step of the graph to stderr as it finishes. It shows counts, rounds, models and problems, never code:

```
[trace] validate_input: input accepted
[trace] run_static_checks: 3 findings
[trace] draft: round 1 on claude-haiku-5-5, 1,900 in / 1,200 out tokens
[trace] verify: 1 problem
[trace]   - public function `add` changed from (a, b) to (x, y)
[trace] refine: round 2 on claude-haiku-5-5, 2,400 in / 1,300 out tokens
[trace] verify: passed
[trace] finalize: done
```

**LangSmith (optional).** LangGraph can also record every run in [LangSmith](https://smith.langchain.com), LangChain's hosted tracing service. It stays off unless you set LangSmith's own variables in your shell:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your LangSmith key>
```

**Traces send the snippet, the analyzer findings and the model's answers to LangSmith's servers.** devassist prints a notice on stderr whenever tracing is on, and a warning if tracing is requested without a key. The Anthropic API key never appears in a trace. LangSmith reads these variables from the shell only; setting them in `.env` has no effect (see `docs/decisions.md` D15).

## Evaluation

The eval in `evals/` measures review quality on 30 labeled cases: 18 with a planted issue, 8 clean controls and 4 adversarial inputs. It compares the shipped design (Haiku, with Sonnet in round 3) with Haiku alone and Sonnet alone, and judges the design against a bar set before any results existed (`docs/decisions.md` D8 and D16). Runs are scored by rule, with no human or model judging.

```
uv run python -m evals.run_evals --dry-run   # estimated cost, no API calls
uv run python -m evals.run_evals --smoke     # the 12 smoke cases
uv run python -m evals.run_evals             # all 30 cases, 3 runs each
```

Every run except `--dry-run` calls the Anthropic API and costs money; the full eval costs about $1 to $3. Results go to `evals/results/`: each run's raw record (`<label>-runs.jsonl`), the summary with the bar's verdict (`<label>-summary.json`) and a table with one row per case (`<label>-cases.csv`). The eval never runs in CI.

## Limitations

- **Verification is static.** Every rewrite must parse, add no new ruff or bandit findings, and keep the original's public functions, classes and methods with the same parameters. Those checks can't tell whether a change is *correct*. When devassist reviewed its own code, Haiku "fixed" a correct import, changing `httpx2` to `httpx` because its knowledge of the Anthropic SDK was out of date, and the rewrite passed every check. Read the improved code before you use it.
- **Python only**, up to 20,000 characters (see `docs/decisions.md` D12).
- **Cost grows with the snippet, not the fix.** The improved code is always the complete snippet, so a one-line fix to a long file still pays for the whole file in output tokens, the expensive kind. Reviewing a 150-line module costs about a third of a cent with Haiku.


## Development

- Install: `uv sync`, then `uv run pre-commit install` to turn on the git hooks.
- Run every check: `uv run pre-commit run --all-files`.

`main` is guarded by a pre-commit hook and by habit rather than GitHub branch protection, which this private repo's plan doesn't include. The `no-commit-to-branch` hook refuses commits to `main`, and pull requests merge only after CI passes.
