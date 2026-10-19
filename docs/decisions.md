# Decision log

One entry per real decision: the context, the options, the choice, why, and what would change it. New entries go at the end. When a decision is replaced, its entry is marked as superseded rather than deleted.

## D1. Python 3.13, managed with uv

*Decided Oct 13, 2026; version settled Oct 14.*

- **Context:** The analyzers (`ast`, ruff, bandit) are Python tools. Development happens on native Windows, and CI runs on Linux and Windows.
- **Options:** pip with venv and a requirements.txt; Poetry; uv. Python 3.12 or 3.13.
- **Choice:** Python 3.13, managed with uv. Dependencies are declared in `pyproject.toml` and locked to exact versions in `uv.lock`. There is no requirements.txt.
- **Why:** Python is my strongest language, and `ast` ships with it. uv installs Python itself, writes one lockfile that covers every OS, and behaves the same in PowerShell and in CI. 3.13 was already installed and every dependency supports it. A hand-written requirements.txt would be a third list of dependencies that nothing checks.
- **Would change if:** a needed dependency dropped 3.13 support. Reviewers without uv get a pip or Docker path on Day 6.

## D2. A command-line interface built with Typer

*Decided Oct 13, 2026.*

- **Context:** devassist is a developer tool that takes a snippet and returns documentation and suggested improvements.
- **Options:** a CLI (argparse, Click or Typer); a web UI (Streamlit); an editor plugin.
- **Choice:** a Typer CLI. A Streamlit front end only if time allows, and an MCP server as a stretch.
- **Why:** A CLI fits how developers already work: files, pipes, scripts and CI. Typer builds the interface from type-hinted functions, so arguments are validated and `--help` is generated. `cli.py` stays a thin adapter, so another front end can reuse the core unchanged.
- **Would change if:** the users weren't developers comfortable in a terminal.

## D3. Orchestration with a LangGraph graph

*Decided Oct 13, 2026.*

- **Context:** The workflow mixes fixed steps (validate, static checks, verify) with model steps (draft, refine) and branches (pass, refine, escalate, give up), capped at 3 rounds.
- **Options:** a plain Python loop; LangChain's `create_agent`; the Claude Agent SDK; a LangGraph `StateGraph`.
- **Choice:** LangGraph, isolated in `agent.py` and built by one `build_graph()` factory that receives the LLM client, analyzers and settings as arguments.
- **Why:** The graph makes the routing explicit, so each route can be tested on its own. It streams each step for `--trace` and draws its own diagram for the README. Agent frameworks such as `create_agent` and the Claude Agent SDK let the model decide which tools to call, but here the checks must run every time. The Agent SDK's strength is running commands and editing files, which this tool must never do with submitted code. A plain loop would work, but the branching would hide in if-statements, and tracing and the diagram would have to be built by hand.
- **Would change if:** the workflow became a straight line with no branches. A plain function would then be simpler.

## D4. Frameworks used and skipped

*Decided Oct 13, 2026.*

- **Context:** The LangChain ecosystem offers orchestration, chat-model wrappers and tracing, and each adds a layer to learn and debug.
- **Options:** the full LangChain stack; LangGraph alone; no frameworks.
- **Choice:** LangGraph for orchestration only. No LangChain chat-model wrappers: the Anthropic SDK is called directly behind the `LLMClient` protocol. LangSmith tracing is opt-in. An MCP server is a stretch goal.
- **Why:** Each tool is used only where it earns its place. Calling the SDK directly gives native structured output (`messages.parse()`) and exact token counts for the cost metrics, with one less layer to debug. Tracing sends snippets to a third party, so it runs only when `LANGSMITH_TRACING=true` and a key are set, and the tests clear those variables.
- **Would change if:** many providers had to be supported quickly. A LangChain adapter could then save time.

## D5. Static verification only

*Decided Oct 13, 2026.*

- **Context:** Every suggested fix is checked before the user sees it, and snippets are untrusted input.
- **Options:** run the code or its tests in a sandbox; static analysis only.
- **Choice:** static checks only: `ast` (does it parse?), ruff (lint) and bandit (security). Submitted code is never executed, imported or passed to `eval`. Analyzers run as subprocesses with argument lists, timeouts and temporary files, never `shell=True`. They run as `python -I -m <tool>` inside the temporary folder, so a stray module in the user's current folder can't run in a tool's place.
- **Why:** Running untrusted code safely needs real isolation (containers, resource limits, no network), which is a project of its own. Static checks catch syntax errors, common bugs and insecure patterns with no execution risk.
- **Would change if:** behavior had to be verified, for example whether a fix still passes the snippet's tests. Sandboxed execution would then be worth its cost.

## D6. Structured output, validated, one retry

*Decided Oct 13, 2026. Amended by D14: requests use `messages.create()` with the same schema, not `messages.parse()`.*

- **Context:** The model's answer feeds code: the verify node, the Markdown and JSON renderers, and the eval's scoring.
- **Options:** free text; JSON parsed by hand; the provider's structured output plus Pydantic validation.
- **Choice:** request structured output with `client.messages.parse()` and the Pydantic model, then validate with Pydantic anyway. On a validation failure, retry once with the error fed back, then raise `SchemaValidationError`. Schemas stay flat.
- **Why:** A typed result is testable and renders the same way every time. Feeding the error back usually fixes a near miss, and capping retries at one bounds cost and latency.
- **Would change if:** the eval shows fewer than 95% of responses valid on the first try.

## D7. Anthropic as the only provider, behind a protocol

*Decided Oct 13, 2026; narrowed to Anthropic only on Oct 14.*

- **Context:** The original plan added OpenAI and Google clients for a rival comparison in the eval.
- **Options:** three providers; Anthropic only; several providers through one LangChain adapter.
- **Choice:** Anthropic only. Every model call goes through the `LLMClient` protocol, and `FakeLLMClient` implements it for tests.
- **Why:** The protocol is what lets every test run offline against a fake client. It also means adding a provider later is one new client class, not a change to the graph. The other providers were cut for time (D9).
- **Would change if:** the eval reopens the model choice, or cross-provider evidence becomes necessary.

## D8. Claude Haiku 5.5, with Claude Sonnet 5.5 as the round-3 fallback

*Decided Oct 13, 2026. Bar set Oct 17, 2026, before any eval results existed.*

- **Context:** Each run makes one to three model calls, so cost and latency add up at scale.
- **Options:** Haiku alone; Sonnet alone; Haiku with escalation to Sonnet; a larger model.
- **Choice:** `claude-haiku-5-5` for rounds 1 and 2, and `claude-sonnet-5-5` in round 3 only. Both models and the escalation round are settings.
- **Why:** Haiku costs a twentieth of Sonnet per token at list price ($0.10 and $0.50 per million input and output tokens, against $2.00 and $10.00, checked Oct 13, 2026). Most snippets should pass within two Haiku rounds, and Sonnet catches the hard cases.
- **Would change if:** the design misses the bar below. The first change to try is escalating in round 2.
- **The bar:** on the 30-case eval, with 3 runs per case, the shipped design stays only if all of these hold against Sonnet alone:
  1. **Issue recall** is no more than 10 percentage points below Sonnet alone's.
  2. **False alarms:** the share of clean-control runs with any reported issue is no more than 10 points above Sonnet alone's.
  3. **Valid on first try:** at least 95% of responses pass validation without the retry (D6).
  4. **Latency:** p95 is under 30 seconds per snippet.
  5. **Cost:** the mean cost per snippet is at most 50% of Sonnet alone's. Since a Haiku call costs about a twentieth of a Sonnet call, this holds while fewer than about 40% of runs escalate.
  6. **Adversarial cases:** every one passes in every run.
- **How it's judged:** each case's 3 runs are averaged first, so the case is the unit, and configurations are compared with a paired bootstrap over cases (95% intervals). Each criterion is decided on the point estimate. If a difference's interval still crosses its margin, the write-up calls that criterion inconclusive at this sample size. The 10-point margins are about two cases wide, the smallest gap 30 cases can resolve.
- **Priorities:** quality first, then cost, with speed as a guardrail. If latency is the only criterion missed, the README reports it, and the design is not changed in ways that reduce quality to meet it.
- **Result (Oct 18, 2026):** the design stays. On the full eval (270 runs, $1.14, `evals/results/full-summary.json`), every criterion holds on its point estimate:
  1. Recall 92.6% against Sonnet alone's 94.4%: −1.9 points (95% CI −11.1 to +5.6). **Inconclusive.**
  2. False alarms 8.3% against 0.0%: +8.3 points (95% CI 0 to +20.8). **Inconclusive.**
  3. 100% of answers valid on the first try. Pass.
  4. p95 latency 13.3 s. Pass.
  5. Mean cost per snippet 7.7% of Sonnet alone's ($0.00090 against $0.01176). Pass.
  6. Every adversarial case passed in every run. Pass.
- **What the result can't show:** no run escalated, because every rewrite passed verification within two Haiku rounds. The eval therefore measured Haiku, not what the Sonnet fallback adds. Since the design and Haiku alone are identical until round 3, their runs measure run-to-run noise: they differed by 3.7 points of recall and 12.5 points of false alarms. That noise is as large as the margins, so the two inconclusive criteria need more cases, not a different design.

## D9. The eval compares three configurations, with no rival models

*Decided Oct 14, 2026.*

- **Context:** The plan also compared the design against GPT-6 Luna and Gemini 3.5 Flash-Lite.
- **Options:** two rivals; one rival; none.
- **Choice:** none. The eval compares the shipped design, Haiku alone and Sonnet alone.
- **Why:** Two more clients, API keys and billing setups didn't fit the six-day schedule. The three configurations still answer the core question: what the fallback saves compared with Sonnet alone, and what it costs compared with Haiku alone. The README compares rivals by list price only, labeled as not measured.
- **Would change if:** time allows after submission, or a reviewer needs cross-provider evidence. GPT-6 Luna would come first, since it is priced the same as Haiku.

## D10. One pull request per task, merged only on green CI

*Decided Oct 13, 2026.*

- **Context:** This private repo is on GitHub's free plan, which has no branch protection.
- **Options:** commit straight to `main`; pull requests by habit alone; pull requests with a local hook and CI.
- **Choice:** one branch and one pull request per task, Conventional Commit messages, and merge commits that keep each pull request visible in the history. Merge only after CI passes on Linux and Windows. The `no-commit-to-branch` pre-commit hook refuses commits to `main`.
- **Why:** Small changes are easy to review and easy to revert. The history shows every change went through a pull request. CI runs on both operating systems because development happens on Windows and most servers run Linux.
- **Would change if:** the repo moved to a paid plan or a team organization, where real branch protection with required checks and reviews would replace the hook.

## D11. Pre-commit hooks run the project's own tools through uv

*Decided Oct 15, 2026.*

- **Context:** Hooks for ruff and mypy can use pre-commit's own isolated copies of each tool or the versions installed in the project.
- **Options:** the published hook repositories (ruff-pre-commit, mirrors-mypy); local hooks that call `uv run`.
- **Choice:** local hooks for `ruff check --fix`, `ruff format` and `mypy src`, all through `uv run`. `no-commit-to-branch` and gitleaks come from their published repositories, pinned to exact versions.
- **Why:** The hooks then use the exact versions in `uv.lock`, the same ones CI uses, so the two can't disagree. pre-commit's isolated mypy can't see the project's dependencies, such as Pydantic, and would report false errors. pytest stays out of the hooks to keep commits fast; CI runs the tests.
- **Would change if:** the hooks became slow enough to get in the way. mypy would then run in CI only.

## D12. Python only

*Decided Oct 16, 2026.*

- **Context:** The brief asks for a tool that reviews code snippets. The analyzers that verify every rewrite (`ast`, ruff, bandit) read only Python.
- **Options:** Python only; several languages, each with its own parser and linters; any language, reviewed by the model alone.
- **Choice:** Python only. `validate_input` rejects any other language with a clear error, before any analyzer or model runs.
- **Why:** Verification is the tool's main guarantee. Accepting a language the analyzers can't read would mean showing rewrites nobody checked. The `Analyzer` protocol keeps the door open.
- **Would change if:** users needed another language. Each one needs its own analyzers (for example ESLint for JavaScript) and its own signature check in `verify.py`.

## D13. How the graph handles failures

*Decided Oct 16, 2026. Amended by D18: with the review on, the step limit has one more step.*

- **Context:** A run can fail in three ways: the input is unusable, a model call fails, or a rewrite still fails verification after the last round.
- **Options:** record every failure in the state; raise every failure; treat each kind on its own terms.
- **Choice:**
  - Invalid input (empty, over the size limit, not Python) sets `error` in the state and ends the graph before any analyzer or model runs.
  - Model and API errors (`LLMError`) raise out of the graph, and the CLI turns them into exit codes.
  - A rewrite that still fails on the last round is returned with its problems as `warnings`.
  - The graph's step limit is exactly what the longest run needs, `2 × max_rounds + 4` steps.
- **Why:** Bad input is the user's to fix, so it's an expected result with a message, not a crash. A failed model call leaves nothing useful to show, and raising keeps the error's type for the CLI. Hiding a rewrite that nearly passed would waste the work; showing it with its problems lets the user judge. LangGraph's default step limit is 10,007, so a routing bug could make thousands of paid model calls before stopping; the exact limit stops it within one run's worth.
- **Would change if:** users preferred no rewrite over a flagged one. `finalize` would then return the original code instead.

## D14. Structured output through `messages.create()`, validated by devassist

*Decided Oct 16, 2026. Amends D6.*

- **Context:** D6 planned to call `messages.parse()`. The SDK's source (anthropic 1.15.0) shows that `parse()` validates the answer inside the SDK as soon as the response arrives, and raises `pydantic.ValidationError` on a failure, discarding the response.
- **Options:** call `parse()` and catch the error; call `create()` with the same schema and validate in devassist; force the JSON shape through tool use.
- **Choice:** `messages.create()` with `output_config.format` set to the schema that `anthropic.transform_schema(Analysis)` builds, so the API still constrains the answer to the schema. devassist then checks `stop_reason` (anything but `end_turn` is an error) and validates with `Analysis.model_validate_json`.
- **Why:** With `parse()`, a refusal or a cut-off answer isn't valid JSON, so it would surface as a schema failure and trigger a paid retry that can't help. The retry D6 requires also sends the bad answer back to the model, and `parse()` throws that answer away. `create()` keeps schema-constrained output and gives devassist the stop reason and the raw text.
- **Would change if:** a later SDK's `parse()` returned the raw response along with a validation failure.

## D15. LangSmith tracing through its own environment variables, shell only

*Decided Oct 17, 2026.*

- **Context:** LangGraph sends traces to LangSmith on its own when LangSmith's environment variables are set. A trace holds the graph's state: the snippet, the analyzer findings and each round's answer. pydantic-settings reads `.env` into `Settings` but doesn't copy it into the process environment, which is where LangSmith looks.
- **Options:** rely on LangSmith's variables, set in the shell; also load `.env` into the environment at startup; read LangSmith settings into `Settings` and attach a tracer by hand.
- **Choice:** LangSmith's own variables, from the shell only. devassist doesn't switch tracing on or off itself. It checks the variables with the same rules the LangSmith library uses, prints a notice on stderr when traces will be sent, and warns when tracing is requested without a key. Tests clear the variables (`tests/conftest.py`).
- **Why:** It takes no code to turn on, and the variables are the ones LangSmith documents. The notice makes sure code never leaves the machine silently. Supporting `.env` would add a second way settings get loaded, plus a dependency, for an optional debugging feature. The Anthropic key isn't part of the graph's state, so it can't appear in a trace.
- **Would change if:** tracing became part of everyday use. `.env` would then be loaded into the environment at startup with python-dotenv, which pydantic-settings already installs.

## D16. How the eval scores runs

*Decided Oct 18, 2026, before any eval results existed. Fills in D8's "How it's judged".*

- **Context:** D8 sets the bar. The runner also needs rules for the edge cases, and they have to be fixed before results can influence them.
- **Options:** for failed runs, leave them out or score them as the worst outcome; for matching, require the exact category or accept any issue on the right lines; for latency and cost, measure every case or only those that reach the model.
- **Choice:**
  - **Matching:** a reported issue matches an expected one when its category is the same and its lines overlap. A miss only on category, such as a SQL injection filed as a `bug`, counts as a miss; such near misses are counted and reported, not credited.
  - **Failed runs:** a run that raises an error (a timeout, an API error, an answer that fails validation twice) scores the worst outcome: recall 0, a false alarm on a clean case, a fail on an adversarial case. Its latency counts. Its cost is unknown, because the error carries no token usage, so the cost comparison uses the cases where both configurations have a known cost.
  - **Valid on first try** counts model answers: a run's rounds minus its retries. A run whose answer fails validation twice adds one invalid answer.
  - **Latency and cost** cover the 28 cases that reach the model. The two inputs devassist rejects return in milliseconds and cost nothing, so they would flatter both.
  - **Order:** runs alternate between configurations case by case, so a slow patch of the API slows all three alike.
  - **Intervals:** 95% percentile bootstrap over cases, 10,000 resamples, with a fixed seed so the numbers reproduce. A criterion is inconclusive when its interval contains its threshold. The adversarial criterion has no interval: one failed run fails it.
- **Why:** each rule closes a way a configuration could look better without being better: failing instead of answering, filing an issue under a vague category, or being measured on easier inputs or during a quieter hour. Writing them down before the first paid run keeps the pre-registration honest.
- **Would change if:** near misses turned out to be common. The write-up would then report recall both ways, with the strict number still deciding the bar.

## D17. A Docker image built from the lockfile and checked in CI

*Decided Oct 18, 2026.*

- **Context:** Reviewers without uv need another way to run devassist (D1). Docker Desktop isn't installed on the development machine, and the deadline is a day away.
- **Options:** pip only; a Docker image checked by hand once; a Docker image built and run in CI.
- **Choice:** a `Dockerfile` on `python:3.13.9-slim` that installs the dependencies with `uv sync --locked --no-dev`, using the same uv version that wrote `uv.lock`. It copies only `pyproject.toml`, `uv.lock`, `README.md` and `src/`, and runs as a non-root user. The API key is passed at run time with `-e`. A CI job builds the image on every pull request and runs the README's command without a key: it must read the shared file, then exit with code 3 and the missing-key message.
- **Why:** The lockfile gives the image the exact versions the tests ran against. Copying named files, plus `.dockerignore`, keeps `.env` and `.git` out of the image. A CI check catches a broken image on every change, where a one-time check by hand would only prove it worked once. The no-key run proves the CLI starts, reads a shared file and has no key built in, without paying for a model call.
- **Would change if:** Docker Desktop were installed. The same command would then be run on Windows once and the README's caveat dropped.

## D18. A second review by a different model, on by default only if it meets D8's bar

*Decided Oct 18, 2026, before any eval run with the review.*

- **Context:** `verify` checks the rewrite, not the review. Nothing at run time checks whether the review missed an issue. On the full eval (D8's result), the design's misses came from two planted issues that no static tool flags: a database query inside a loop (`performance-query-per-item`) and a missing docstring (`documentation-missing-docstring`). On the README's demo file, one review reported 2 of 4 planted problems, missing the same two kinds.
- **Options:** no run-time check, with misses measured offline only (as built until now); a second pass by the same model; a second review by a different Anthropic model; a second review by another company's model, such as GPT-6 Luna; a second reviewer that rewrites the whole answer rather than listing what's missing.
- **Choice:** a `review` node that asks Claude Sonnet 5.5 (`DEVASSIST_REVIEW_MODEL`) for real problems the latest passing answer missed. It sees the numbered snippet and the reported issues, not the static findings. Its prompt goes through bugs, security, performance and documentation in turn and leaves out style. It returns only the missed issues; any it finds go back to `refine`, which adds and fixes them in a round that `verify` checks like any other. The review runs once per run, after a pass, and only while a round is left, so the round cap still bounds the run, with at most one extra call. With the review on and a cap above one, the step limit becomes `2 × max_rounds + 5`. `DEVASSIST_REVIEW` and `--review` turn it on.
- **Why:**
  - **A different model:** reviewing one model's work with another is the usual way to catch its blind spots, because their errors are less likely to coincide. Within one company they may still overlap. On the eval, Sonnet alone also missed the N+1 query in all 3 runs, though it found the missing docstring in 3 of 3, against Haiku's 1 of 6.
  - **Missed issues only:** a short answer keeps the review cheap, and the fix still goes through `verify`.
  - **No tool findings:** the first review already had them. Leaving them out keeps the reviewer from anchoring on what's already covered.
  - **Not another company's model, for now:** its errors would likely be more independent, and GPT-6 Luna costs about what Haiku does. But it needs a second SDK, and a second API key for anyone running devassist, and it sends snippets to a second company (D7). It didn't fit the deadline.
- **How it's decided, set before any run:** the eval's design configuration gets the review; Haiku alone and Sonnet alone stay single-pass, so Haiku alone now shows what the review adds. The review ships on by default only if the design with the review meets all six of D8's criteria, judged as D8 and D16 describe, with the bar unchanged. Otherwise it ships off by default, behind `--review`, and the README reports which criterion it missed. Two criteria are at risk:
  - **Cost:** a Sonnet call in every run, estimated before measurement at roughly 45 to 60% of Sonnet alone's cost, against the 50% limit.
  - **False alarms:** a reviewer told to look for misses may add nitpicks on clean code.
- **What the eval can't show:** the reviewer's checklist was written after seeing which issues the design missed. It names categories, not cases, but gains on those two cases are partly in-sample. The 30 cases each plant one issue, so they barely test snippets with many problems, where a second review should matter most.
- **Would change if:** with more time and budget, a reviewer from another company, starting with GPT-6 Luna, would be measured against the Sonnet reviewer on new multi-issue cases. If the review misses the bar only on cost, a cheaper reviewer comes first.
