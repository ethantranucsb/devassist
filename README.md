# devassist

A command-line assistant that documents and reviews Python snippets, checking every suggestion with static analysis.


## Development

- Install: `uv sync`, then `uv run pre-commit install` to turn on the git hooks.
- Run every check: `uv run pre-commit run --all-files`.

`main` is guarded by a pre-commit hook and by habit rather than GitHub branch protection, which this private repo's plan doesn't include. The `no-commit-to-branch` hook refuses commits to `main`, and pull requests merge only after CI passes.
