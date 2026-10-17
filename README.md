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

Results go to stdout and errors to stderr, so `> review.md` captures only the review. Output is always UTF-8. Piping works in any shell, but Windows PowerShell 5.1 pipes text as ASCII by default, so pass a file path there if the code has non-ASCII characters.

| Option | What it does |
|---|---|
| `--format markdown\|json` | How to print the review (default `markdown`). |
| `--model NAME` | Model for the rounds before escalation. Overrides `DEVASSIST_MODEL`. |
| `--max-rounds N` | Most model answers per run (default 3). Overrides `DEVASSIST_MAX_ROUNDS`. |
| `--language NAME` | The snippet's language. Only Python is supported. |
| `--verbose`, `-v` | Log escalations and retries to stderr. |

Settings come from command-line flags first, then environment variables, then `.env`, then the defaults in `.env.example`.

| Exit code | Meaning |
|---|---|
| 0 | Success, including a review with warnings. |
| 1 | An unexpected error (a bug). |
| 2 | A usage or input problem: missing, unreadable, non-UTF-8, empty or oversized input, or another language. |
| 3 | A configuration problem, such as a missing API key or an invalid setting. |
| 4 | A model or API problem: timeout, rate limit, refusal, or an answer that failed validation twice. |
| 5 | A static analyzer failed. |


## Development

- Install: `uv sync`, then `uv run pre-commit install` to turn on the git hooks.
- Run every check: `uv run pre-commit run --all-files`.

`main` is guarded by a pre-commit hook and by habit rather than GitHub branch protection, which this private repo's plan doesn't include. The `no-commit-to-branch` hook refuses commits to `main`, and pull requests merge only after CI passes.
