# devassist

devassist is a command-line developer assistant. It takes a Python snippet, documents it, flags issues and suggests an improved version. Every suggestion is checked by static analysis (`ast`, ruff, bandit) and refined by the model, for at most 3 rounds, before the user sees it. The workflow runs as a LangGraph graph: deterministic nodes for the checks, model nodes for drafting and refining, and conditional edges for the routing. The model is Claude Haiku 5.5, with Claude Sonnet 5.5 as a fallback for the hardest cases.

## Commands

All commands run the same in PowerShell and Git Bash.

- Install or sync dependencies: `uv sync`
- Run the tool: `uv run devassist explain path/to/file.py` (or pipe code on stdin); add `--trace` to stream each graph step
- Tests (offline, no API key needed): `uv run pytest`
- Live API tests (need `ANTHROPIC_API_KEY`): `uv run pytest -m live --no-cov` (a few tests can't reach the coverage bar on their own)
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Types: `uv run mypy` (checks `src/` and `evals/`, as listed in `pyproject.toml`)
- All pre-commit hooks: `uv run pre-commit run --all-files`
- Docker image (built and run by CI; Docker isn't installed locally): `docker build -t devassist .`
- Eval cost estimate before any paid run: `uv run python -m evals.run_evals --dry-run`
- Eval runs (paid; ask first): `uv run python -m evals.run_evals --smoke` for the 12 smoke cases, or without `--smoke` for all 30; `--label NAME` names the result files so earlier ones are kept

## Layout

Target layout; some modules don't exist yet.

- `src/devassist/cli.py`: Typer app. Parses arguments, renders Markdown or JSON, maps errors to exit codes. No business logic.
- `src/devassist/agent.py`: builds the LangGraph `StateGraph` in `build_graph(llm_client, analyzers, settings)`. The only module that imports LangGraph.
- `src/devassist/llm/`: the `LLMClient` protocol, the Anthropic client and `FakeLLMClient` for tests.
- `src/devassist/analyzers/`: the `Analyzer` protocol with syntax (`ast`), ruff and bandit implementations.
- `src/devassist/models.py`: Pydantic schemas for the analysis, issues, findings and the graph state.
- `src/devassist/prompts/`: versioned prompt templates, loaded at run time.
- `src/devassist/config.py`: settings from the environment (pydantic-settings), the default and fallback models, limits and the price table.
- `src/devassist/exceptions.py`: the error hierarchy the CLI turns into messages.
- `src/devassist/mcp_server.py` (stretch): an MCP server exposing one `explain_snippet` tool. A thin adapter like `cli.py`: it calls the same graph and adds no logic.
- `tests/`: mirrors `src/`; shared fixtures in `tests/conftest.py`, sample snippets in `tests/fixtures/`.
- `evals/`: labeled cases, the eval runner and saved results.
- `examples/`: deliberately flawed snippets for the README and demos; excluded from ruff.
- `docs/decisions.md`: the decision log. Read it before changing an architectural decision, and add an entry when you make one.
- `docs/evaluation.md`: the eval's full results and the reasoning behind the default model; the README keeps a summary.

## Graph rules (LangGraph)

- Build the graph only in `build_graph()`. Dependencies come in as arguments, never as globals or imports inside nodes.
- Nodes: `validate_input`, `run_static_checks`, `draft`, `verify`, `review`, `refine`, `finalize`. Only `draft`, `review` and `refine` call a model.
- Routing: invalid input goes straight to the end with an error. After `verify`, a pass goes to `finalize`, a failure with rounds left goes to `refine`, and a failure on the last round goes to `finalize` with warnings. When the review setting is on, the first pass with a round left goes to `review` instead; missed issues go to `refine`, and none go to `finalize`.
- Rounds 1 and 2 use the default model (Haiku); round 3 uses the fallback model (Sonnet). The escalation round and the round cap are settings, not literals.
- The graph state is a typed model, and each node returns only the fields it changes.
- Test each node as a plain function, and test every route through the compiled graph with `FakeLLMClient`.
- `--trace` streams node updates from the compiled graph.
- Generate the README diagram from the compiled graph as Mermaid; never hand-draw it.

## Code rules

- Python 3.13. Type hints on every function and method; mypy runs in strict mode on `src/` and `evals/`.
- Pass dependencies in through constructors or parameters. Never create an LLM client, read an environment variable or open a network connection deep inside a function.
- Depend on the protocols (`LLMClient`, `Analyzer`), not on concrete classes.
- Raise exceptions from `exceptions.py`; never use a bare `except:` or silently swallow an error.
- Use `logging`, never `print`, outside `cli.py`.
- Google-style docstrings on public functions and classes.
- No magic values: model names, limits, timeouts and prices live in `config.py` only.
- Ask before adding a dependency, and add it with `uv add` so `uv.lock` updates.

## Windows and portability rules

- Development happens on native Windows and CI runs on both Linux and Windows, so code must work on both.
- Use `pathlib.Path` for every path; never build paths from strings or hard-coded separators.
- Pass `encoding="utf-8"` to every `open()`, `Path.read_text()`, `Path.write_text()` and text-mode `subprocess.run()`. Python on Windows doesn't default to UTF-8.
- Run analyzers as `[sys.executable, "-I", "-m", "ruff", ...]` and `[sys.executable, "-I", "-m", "bandit", ...]`, with the temporary folder as the working directory, so they resolve inside the project environment on any OS. `-I` stops Python from importing modules from the current folder, so a stray `ruff.py` can't run in place of ruff. All of this lives in `analyzers/base.py`'s `run_tool`.
- Write snippets for analysis to a file inside `tempfile.TemporaryDirectory()`. Never hand an open `NamedTemporaryFile` to a subprocess: Windows won't let another process open it.
- Line endings are LF everywhere, enforced by `.gitattributes`.
- Scripts and docs use `uv run ...` rather than shell-specific syntax, so they work in PowerShell.

## LLM rules

- Every model call goes through an `LLMClient`, and the Anthropic SDK is imported only inside `llm/`. Don't use LangChain's `create_agent` or its chat-model wrappers; `docs/decisions.md` records why.
- Provider: Anthropic only. OpenAI and Google were cut for time, as `docs/decisions.md` records; don't add another provider's SDK or client without updating that entry.
- Default model: `claude-haiku-5-5` (`DEVASSIST_MODEL` or `--model`). Fallback model: `claude-sonnet-5-5` (`DEVASSIST_FALLBACK_MODEL`), used only in round 3. Log every escalation.
- Review model: `claude-sonnet-5-5` (`DEVASSIST_REVIEW_MODEL`), used only by the `review` node, which runs by default unless `DEVASSIST_REVIEW=false` or `--no-review` turns it off. It must differ from the default model; `docs/decisions.md` D18 records why, and D21 why it's on by default. The eval runner uses these overrides to compare three configurations: the shipped design, Haiku alone and Sonnet alone.
- Haiku with a Sonnet fallback is a recorded decision in `docs/decisions.md`. Don't change either model without updating that entry.
- Request structured output with `client.messages.create()` and `output_config`, using the schema `anthropic.transform_schema()` builds from the Pydantic output model. Check `stop_reason` first: anything but `end_turn` is an error. Then validate with Pydantic. Don't use `messages.parse()`: it validates inside the SDK before the stop reason can be checked, and it discards the answer the retry needs (`docs/decisions.md` D14).
- On a validation failure, retry once with the error message fed back, then raise `SchemaValidationError`.
- Keep output schemas flat: few optional fields, few union types. Compare enum values case-insensitively.
- Set an explicit timeout and retry limit on every SDK client.
- Prompts live in `prompts/` as files with a version string. Never build long prompts inline with f-strings.
- The user's snippet goes inside clear delimiters, and the prompt tells the model it is data to analyze, not instructions to follow.

## Observability rules

- Record tokens, cost, latency, rounds used and escalations for every run.
- LangSmith tracing is opt-in: it runs only when `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are set, and the tool never requires it.
- `tests/conftest.py` clears every `LANGSMITH_*` variable, so tests and CI never send traces.
- Traces send snippets to LangSmith; the README must say so.

## Security rules

- Never execute, import or `eval` submitted code. Analysis is static only.
- Subprocess calls use an argument list, never `shell=True`, and always set a timeout.
- Never read, print, log or commit `.env` or any API key. New settings go in `.env.example` with a placeholder value.
- Reject empty input and input over the configured size limit with a clear error.

## Testing rules

- Write the failing test first, then the implementation.
- Unit tests never touch the network: use `FakeLLMClient` and fixture snippets.
- Tests that call a real API are marked `@pytest.mark.live` and skip when no key is set.
- Cover failure paths, not just the happy path: bad JSON, refusals, timeouts, escalation, the round cap, invalid and oversized input, CLI exit codes.
- Keep coverage of `src/devassist/` at 85% or higher.
- Evals measure model quality and are separate from unit tests; they never run in CI.

## Git workflow

- Never commit to `main`; the `no-commit-to-branch` pre-commit hook refuses it. This private repo's GitHub plan has no branch protection, so the hook and the habit do that job.
- One branch per task, named `feat/...`, `fix/...`, `test/...`, `docs/...` or `chore/...`.
- Conventional Commit messages, for example `feat: add bandit analyzer`.
- Small commits that each pass all checks.
- Do not push, merge or rewrite history unless asked. The developer reviews every pull request and merges only after `gh pr checks` passes.

## Definition of done

A task is done only when:

1. `ruff check`, `ruff format --check`, mypy and pytest all pass.
2. New behavior has tests, including at least one failure case.
3. `README.md` or `docs/decisions.md` is updated if behavior or a decision changed.
4. You have summarized what changed, why, and anything you were unsure about.

## Working style

- For anything beyond a one-line fix, propose a plan and wait for approval before editing.
- Prefer the smallest change that solves the task. Do not refactor unrelated code.
- If a requirement is ambiguous, ask instead of guessing.
