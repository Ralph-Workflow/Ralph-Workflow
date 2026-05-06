# Ralph Workflow (Python)

> Vendor-neutral AI coding workflow orchestration — unattended, auditable, and configured in your repo.

Ralph Workflow is a Python 3.12+ CLI package and framework for **policy-defined orchestration** of AI coding workflows. You decide which agent runs which phase, keep the workflow configuration in repo-local TOML, and let Ralph Workflow plan, implement, and commit work for you. The runtime is a generic policy interpreter: all workflow behavior — phase routing, retry rules, analysis loops, commit semantics, verification gates, recovery routing, and parallel execution constraints — is declared in TOML policy files and enforced by the runtime without hardcoded phase knowledge.

The package exposes two entry points:

- `ralph` — the main CLI
- `ralph-mcp` — the standalone MCP server runtime

## What Ralph Workflow is

Ralph Workflow sits across AI coding vendors rather than locking you into one tool.
It can route work across Claude Code, Codex, OpenCode, and any OpenCode-wrapped model,
so you can use frontier models where reasoning matters and cheaper models where they are enough.

Key differentiators:

- **Vendor-neutral orchestration** — choose different agents for planning, development, and commit; custom policies can add review and fix phases
- **Cost arbitrage** — route frontier models to planning and cheaper models to development
- **Unattended execution** — walk away and come back to a finished diff instead of babysitting an agent
- **Workflow config in repo** — phase graph, agent chains, retry budgets, and recovery rules live in versioned config
- **Recovery and verification discipline** — checkpoint/resume, failure classification, and evidence-based phase completion

## Policy-Driven Orchestration

Ralph Workflow's pipeline behavior is defined entirely by three TOML policy files:

| File | What it declares |
|------|------------------|
| `.agent/pipeline.toml` | Phase graph, roles, transitions, retry rules, analysis loops, commit semantics, recovery routing, parallel execution |
| `.agent/ralph-workflow.toml` | Agent chains and drain-to-chain bindings |
| `.agent/artifacts.toml` | Artifact contracts and decision vocabularies per drain |

The runtime validates that policy is semantically complete at startup and rejects incomplete configurations with actionable errors — it does not silently fall back to hidden built-in semantics.

**Policy surfaces that are configurable:**

- Phase roles (`execution`, `analysis`, `review`, `commit`, `verification`, `terminal`, `fanout_join`)
- Transition graph (`on_success`, `on_failure`, `on_loopback`)
- Analysis loop bounds and iteration counters
- Decision vocabulary and per-decision routing
- Budget counters and the commit counter that each commit phase increments
- Post-commit budget-guarded routing (`remaining`, `exhausted`, `no_review`)
- Retry and fallback strategy per phase
- Recovery cycle cap and terminal routing
- Parallel execution constraints
- Artifact requirements per drain

**To understand why Ralph Workflow routed a certain way**, read the active `.agent/pipeline.toml` — all routing decisions trace back to declared policy, not code branches.

**To add or change workflow behavior**, update `pipeline.toml`. Incomplete policy is rejected at startup with a `PolicyValidationError` listing the missing fields.

**To override a budget counter cap at runtime** without editing `pipeline.toml`, use the `--counter` flag:

```bash
ralph --counter iteration=2         # limit developer cycles to 2
```

Counter names must match `[budget_counters.<name>]` entries declared in `pipeline.toml`. Use `--check-policy` to confirm effective caps after overrides.

## Inspecting the active policy

Run `ralph --explain-policy` to print a visual representation of the active pipeline policy:

```bash
ralph --explain-policy
```

To inspect a project-local policy directory explicitly:

```bash
ralph --explain-policy --explain-policy-dir /path/to/.agent
```

Example output (abbreviated):

```
=ENTRY=>
+------------------+
|    planning      |
| role=execution   |
+------------------+
    |
    v
[fanout: max_workers=8, max_units=50]
+------------------+
|   development    |
| role=execution   |
+------------------+
    | loop back to development
    +---^  (returns to 'development' phase)
    |
    v
...
+------------------+
|    complete      |
| role=terminal    |
+------------------+
==SUCCESS==>
```

The diagram shows phases and their roles, the happy-path spine, loopback arrows with their return targets clearly marked, decision branches, fanout annotations for parallel phases, and terminal outcome markers. See [docs/migration/policy-v2.md](../docs/migration/policy-v2.md) for the policy model reference and migration guide.

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

Requires Python 3.12+.

### pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e .
ralph --version
```

## Quick start

```bash
cd /path/to/your/project
ralph --init
# edit PROMPT.md
ralph
```

`ralph --init` is the canonical form. Compatibility labels such as `default` are deprecated,
ignored, and no longer recommended in docs or scripts. `ralph --init` scaffolds the project-local
support files; use `ralph --generate-local-config` only when this repo needs a main-config override
instead of inheriting from `~/.config/ralph-workflow.toml`.

## First-run configuration

On first run, Ralph Workflow creates the standard project and user config files from bundled templates.

**User-global (created once, reused across projects):**
- `~/.config/ralph-workflow.toml` — main Ralph Workflow configuration
- `~/.config/ralph-workflow-mcp.toml` — MCP servers, web search, and web visit configuration

**Project-local support files (created by `ralph --init`, live in your project directory):**
- `.agent/mcp.toml` — project-local MCP override
- `.agent/pipeline.toml` — phase graph and orchestration settings
- `.agent/artifacts.toml` — MCP artifact contracts per drain

**Optional project-local main override (created only when you ask for it):**
- `.agent/ralph-workflow.toml` — project-specific main config override, including agent chains and drain bindings; generate it with `ralph --generate-local-config`

**Override precedence (highest to lowest):**
CLI flags → project-local (`.agent/`) → user-global (`~/.config/`) → bundled defaults

To reset configs from the bundled defaults (existing files are backed up to `<name>.bak`), run:

```bash
ralph --regenerate-config
```

Before your first real run, it is a good idea to validate your environment:

```bash
ralph --diagnose
```

## How a run works

When you run `ralph`, the workflow moves through a structured sequence of phases:

1. **Planning** — a planning agent reads `PROMPT.md` and produces a structured plan
2. **Planning analysis** — the workflow checks whether the proposed plan is executor-ready or needs another planning pass; when it sends planning back for revision, Ralph Workflow surfaces the prior planning-analysis feedback so the planner can edit the existing plan incrementally via the plan-draft MCP tools
3. **Development** — a developer agent implements the work
4. **Development analysis** — the workflow decides whether to iterate or continue
5. **Development commit** — changes are committed; if iterations remain (derived from cap minus completed progress), the loop returns to planning for another cycle
6. **Complete** — the workflow ends successfully

Custom policies declared in `.agent/pipeline.toml` can add review, fix, or any other phase. The default bundled policy is a clean planning → development loop.

## Compatible agents

Ralph Workflow supports three built-in transport families and several naming forms on top of them.

| Identifier form | What it means | Example |
|---|---|---|
| `claude` | Claude Code using your currently selected Claude Code model/profile | `planning = ["claude"]` |
| `claude/<family>` | Force a Claude model family for that chain entry | `planning = ["claude/opus"]` |
| `codex` | OpenAI Codex CLI transport | `review = ["codex"]` |
| `opencode` | Base OpenCode transport | `development = ["opencode"]` |
| `opencode/<provider>/<model>` | OpenCode with an explicit provider/model target | `development = ["opencode/minimax/MiniMax-M2.7-highspeed"]` |
| `ccs/<alias>` | Claude Code Switch alias resolved dynamically | `planning = ["ccs/work"]` |
| custom `[agents.*]` name | Your own named agent definition in `ralph-workflow.toml` | `review = ["my-reviewer"]` |

Built-in transports:

| Transport | Strong at | Setup |
|---|---|---|
| Claude Code | Planning, complex reasoning, large context | `npm install -g @anthropic/claude-code` |
| Codex CLI | Structured review, cost-effective analysis | `npm install -g @openai/codex` |
| OpenCode | Multi-provider execution across OpenCode-supported models | [opencode.ai](https://opencode.ai) |
| CCS | Profile-based Claude Code switching and aliasing | Use `ccs/<alias>` directly |

## When Ralph Workflow fits

- Multi-step coding tasks that do not fit in one prompt
- Refactors, test suites, docs, or features that take longer unattended runs
- Work where you want to walk away and come back to reviewed commits
- Teams that need cost-controlled, auditable, or workflow-configured agent execution
- Anyone tired of paying frontier-model rates for grunt work cheaper models handle fine

## When it does not fit

- One-shot prompts you can answer interactively
- Pair-programming sessions where you want to steer in real time
- Tasks that finish manually before setup overhead pays off
- Workflows that need unpredictable mid-run human input

## Standalone MCP runtime

The package also ships `ralph-mcp`, a standalone MCP HTTP server runtime:

```bash
ralph-mcp --help
```

Use it when you want Ralph Workflow's MCP tool surface without running the full `ralph` pipeline.

## Verification

```bash
make verify
```

That runs:

- `make lint` (`ruff check ralph/ tests/`)
- `make typecheck` (`uv run python -m mypy ralph/`)
- `make docs` (`uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going`)
- `make test-cov` (`uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under 80`)
- `make test-subprocess-e2e`

If any step fails, `make verify` prints a high-visibility banner that cites `AGENTS.md` and `CLAUDE.md` and tells the active AI agent to fix the failure immediately before doing anything else.

For narrower local runs, use:

- `make docs` — build Sphinx HTML into `docs/sphinx/_build/html` with warnings treated as errors
- `make test` — full pytest suite without coverage
- `make test-unit` — everything under `tests/` except `tests/integration/`
- `make test-integration` — `tests/integration/` only

For the dead-code policy tooling, run the separate Vulture audit:

```bash
make dead-code
```

It is intentionally separate from `make verify` while the current dead-code backlog still exists; today the command proves the scanner wiring by failing on the code that still needs cleanup.

## Package map

- `ralph/cli/` — Typer CLI entry points and command plumbing
- `ralph/config/` — layered config loading and Pydantic models
- `ralph/pipeline/` — state, events, reducer, orchestrator, effects
- `ralph/phases/` — phase handlers and dispatch
- `ralph/agents/` — agent registry, chains, and invocation
- `ralph/mcp/` — MCP bridge, artifact handling, standalone server runtime
- `ralph/git/` — GitPython-backed repository helpers and rebase support
- `ralph/workspace/` — production and in-memory filesystem abstractions
- `ralph/recovery/` — failure classification, budgets, connectivity monitoring, and recovery controller

## Pydoc-first API reference

The public package docstrings are intended to stand on their own. Useful entry points:

```bash
python -m pydoc ralph
python -m pydoc ralph.cli
python -m pydoc ralph.pipeline
python -m pydoc ralph.mcp
python -m pydoc ralph.git
python -m pydoc ralph.workspace
python -m pydoc ralph.recovery
```

Use package/module docstrings for API understanding and this README for workflow-level guidance.

## Phase-output hardening

Ralph Workflow now treats several agent-driven phases as producing explicit evidence, not just a zero exit code.

- In same-workspace parallel mode, `development` workers (and custom `fix` workers) are judged by per-worker artifact evidence only: a worker succeeds when it submits an artifact under `.agent/workers/<unit_id>/artifacts/`. Repo-wide `git status` is never used to determine worker success in parallel mode. Exit code is retained as diagnostic information only.
- Custom review phases (declared in `pipeline.toml`) must leave behind a fresh `.agent/artifacts/issues.json`.
- Planning keeps `.agent/artifacts/plan.json` as the canonical machine-readable artifact and mirrors it to `.agent/PLAN.md` as the human/agent handoff.
- The runner still removes per-phase artifacts before each invocation so interrupted runs cannot leak stale summaries or review findings into later phases.

Artifact contract:
- Use `.json` artifacts for Ralph Workflow's validation, routing, checkpointing, and other orchestrator-only logic.
- Use `.md` handoff files when a user or downstream AI agent needs to read the result of an earlier phase.
- Current mirrored handoffs are:
  - `.agent/PLAN.md`
  - `.agent/DEVELOPMENT_RESULT.md`
  - `.agent/DEVELOPMENT_ANALYSIS_DECISION.md`
  - `.agent/ISSUES.md` *(custom review phase)*
  - `.agent/FIX_RESULT.md` *(custom fix phase)*
  - `.agent/REVIEW_ANALYSIS_DECISION.md` *(custom review analysis phase)*

This hardening is intentionally selective. Planning relies on explicit artifacts where Ralph Workflow needs structured evidence. In same-workspace parallel mode, `development` workers are judged by per-worker artifact evidence under `.agent/workers/<unit_id>/artifacts/`; repo-wide workspace changes are not used as a success signal in parallel mode. Custom review/fix phases follow the same contract for their own artifacts.

## Built-in web tools

### Web search (`web_search`)

Enabled by default. Uses a multi-backend fallback chain (ddgs, Tavily, Brave, Exa, SearXNG).
Configure via `[web_search]` in `mcp.toml`.

### URL fetching (`visit_url`)

A built-in `visit_url` tool fetches a single HTTP/HTTPS page and returns readable extracted text.
Requires the optional extras:

```bash
pip install "ralph-workflow[web-visit]"
```

Configure via `[web_visit]` in `mcp.toml`.
See [`docs/mcp/web-visit.md`](docs/mcp/web-visit.md) for the full reference.

For multi-page or JavaScript-rendered crawling, wire in [Crawl4AI](https://docs.crawl4ai.com/)
as an upstream MCP server — see [`docs/mcp/mcp-servers.md`](docs/mcp/mcp-servers.md).

## Multimodal MCP support (default-on)

Ralph Workflow supports image-reading MCP tools via `read_image`. This feature is **enabled by default**.

To disable it, add to `.agent/mcp.toml`:

```toml
[media]
enabled = false
```

When enabled (default):
- supported formats are PNG, JPEG, GIF, and WebP
- `read_image` only appears for clients that declare multimodal/image/media capability
- text-only clients keep the pre-multimodal tool set unchanged
- image payloads are returned as MCP image content blocks with base64-encoded data
- `max_inline_bytes` enforces the inline size guard (5 MiB by default)

To customize, add to `.agent/mcp.toml`:

```toml
[media]
enabled = true  # (default, can be omitted)
max_inline_bytes = 10485760  # 10 MiB to allow larger images
```

### Compatibility contract

The multimodal support is designed with strict backward compatibility:

1. **Text-only clients unchanged** — Existing tools (`read_file`, `write_file`, etc.) continue to return text content blocks with the same shape.
2. **Client capability filtering** — `read_image` only appears in `tools/list` for clients that declare multimodal/image/media capability in the MCP `initialize` handshake.
3. **Upstream multimodal rejection** — If an upstream MCP server returns a non-text content block, Ralph Workflow rejects it with a clear error rather than silently passing it through.

### What text-only clients see

When a client connects without declaring multimodal support, `read_image` is **not visible** in `tools/list`, even if `media.enabled = true`. The text-only tool set is byte-equivalent to pre-multimodal behavior.

### What multimodal clients see

Clients that declare `capabilities.image`, `capabilities.media`, or `capabilities.multimodal` in the `initialize` request will see `read_image` in `tools/list` when `media.enabled = true`.

## Claude/CCS MCP safety note

Claude-compatible transports such as `claude` and `ccs` run through a stricter MCP path. Ralph Workflow still uses `--mcp-config` plus `--strict-mcp-config`, but it only emits `--tools ""` / `--allowedTools ...` when live MCP tool discovery succeeds with a non-empty allowlist. That avoids a brittle edge case in non-interactive Claude/CCS runs where empty-tool configurations and MCP bootstrapping can produce misleadingly successful no-op executions.

Ralph Workflow's Claude parser accepts both bare (`claude: ...`) and model-qualified (`claude/<model>: ...`) transcript prefixes emitted by the Claude CLI. Lifecycle-only markers (`message_delta`, `user`, `system (status=...)`, `thinking` without a payload) are automatically suppressed so they never appear as noise in the activity log. Free-form text and tool lines after the prefix are parsed normally.

## Parallel mode

When the planning phase produces two or more work units, Ralph Workflow runs them as parallel workers in the **same git checkout** (same-workspace mode v1). Each worker is restricted to its declared `allowed_directories` and writes its artifacts under `.agent/workers/<unit_id>/`. Workers share the checkout and write to it directly, without separate git branches; coordination uses edit-area fencing and artifact namespaces only. For the full guide including configuration, work unit structure, and success criteria, see [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md).

Quick configuration (in `.agent/pipeline.toml`):

```toml
[phases.development.parallelization]
mode = "same_workspace"
max_parallel_workers = 4
max_work_units = 50
```

See [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md) for the full guide.

## Recovery

Ralph Workflow treats failure recovery as a first-class concern. It supports checkpoint/resume,
failure classification, retry budgets, and connectivity-aware pause/resume behavior.

See [`docs/sphinx/recovery.md`](docs/sphinx/recovery.md) for the full guide.

## Long-content display

When agent output gets large, Ralph Workflow keeps the terminal readable by summarizing oversized content.
The deterministic headline summary layer is **enabled by default** and activates once content exceeds **4000** display cells.
That summary appears before the condensed output and gives you a stable, deterministic headline instead of making you scroll through a giant block.

If no clean headline can be extracted, Ralph Workflow shows the placeholder **`(no headline available)`**.
Inline summary lines are capped at **200** characters, and streaming end-line summaries are capped at **120** characters.

To disable the deterministic headline layer, use any of these values for `RALPH_LONG_CONTENT_SUMMARY`:
`0`, `false`, `no`, or `off`.
It is already on by default, so you do not need an "enable" value for it.

Ralph Workflow also supports an optional AI-generated summary layer labelled **`↳ ai-summary:`**.
That layer is controlled separately through the `RALPH_LONG_CONTENT_AI_SUMMARY` opt-in environment variable.

## Terminal display

All rendering goes through a single `DisplayContext` object that owns the console, colour
policy, terminal width, and adaptive layout limits. No renderer constructs its own console.

**Modes**

| Mode | Trigger | Headline cap | Condenser soft limit |
|------|---------|--------------|---------------------|
| `compact` | width < 60, or `RALPH_FORCE_NARROW=1` | 80 chars | 240 cells |
| `medium` | width 60–99 | 100 chars | 300 cells |
| `wide` | width ≥ 100 (default) | 120 chars | 400 cells |

In `compact` mode, secondary columns, extra blank lines, and descriptive rules are suppressed
to fit narrow terminals. In `medium` and `wide` modes, phase-start and phase-close banners
include additional context lines and fuller iteration labels. Major phase transitions (e.g.
execution→analysis, analysis→commit) render as a single titled horizontal Rule in all modes,
showing the routing decision and analysis-status hint when applicable.

**Phase-start banners** show iteration context as `Dev N/cap` (outer development cycle, 1-indexed)
and `Analysis N/cap` (inner analysis loop, 1-indexed). `Dev 1/5` means the pipeline is entering
its first development cycle out of five total. In **medium and wide mode**, `(outer)` and `(inner)`
qualifiers are appended to the dev and analysis cycle labels to make the distinction explicit.
In **wide mode**, a titled rule separator precedes the banner and the agent name appears on its
own indented line for readability.

**Phase-close banners** are symmetric with phase-start: same field ordering and glyphs, followed
by elapsed time (e.g. `7.5s`) and the exit trigger (e.g. `→ produced`, `→ completed`). In
**medium and wide mode**, two additional lines appear below the header when applicable:
- `↳ stats:` — content/thinking/tool/error activity counters (omitted when all are zero)
- `↳ artifact:` — what the phase produced (e.g. `plan: 5 step(s), 2 risk(s)`)

For review phases, a `review: clean` or `review: issues found` line is always shown regardless
of display mode. When a phase ends with debug breadcrumbs still set (waiting-status or failure
category), a `debug:` line is appended immediately below the close banner in all display modes —
making failure context visible without reading the completion summary.

In **wide mode**, a trailing horizontal Rule separator is printed after the close banner,
symmetrically closing the phase section opened by the titled Rule from the phase-start banner.

See [`docs/sphinx/transcript.md`](docs/sphinx/transcript.md) for the full phase-start, phase-close,
and `[run-end]` format specifications.

**Environment knobs**

| Variable | Effect |
|----------|--------|
| `RALPH_FORCE_NARROW=1` | Force compact mode regardless of width |
| `NO_COLOR=1` | Disable colour output (standard, wins over `FORCE_COLOR`) |
| `FORCE_COLOR=1` | Force colour output even in non-TTY environments |
| `COLUMNS=<n>` | Override terminal width used for mode detection |
| `RALPH_FORCE_ASCII=1` | Use ASCII glyph fallbacks (e.g. `->` instead of `→`) |
| `RALPH_STREAMING_DEDUP=0` | Disable deduplication of consecutive identical streaming fragments |
| `RALPH_STREAMING_CHECKPOINTS=0` | Disable periodic checkpoint lines during long streaming blocks |

Colours come from the Okabe-Ito palette defined in `ralph/display/theme.py` and are
applied through semantic theme keys (`theme.banner.title`, `theme.text.muted`, etc.).
This makes it straightforward to adapt the palette without touching renderer code.

## Documentation

The full documentation lives in `docs/sphinx/` and is published under `/docs` at <https://ralphworkflow.com>.
Useful pages:

- [`docs/sphinx/getting-started.md`](docs/sphinx/getting-started.md) — step-by-step first-run walkthrough
- [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md) — install, init, and run in five minutes
- [`docs/sphinx/concepts.md`](docs/sphinx/concepts.md) — phases, drains, agents, MCP artifacts, checkpoints
- [`docs/sphinx/cli.md`](docs/sphinx/cli.md) — all CLI flags and sub-commands
- [`docs/sphinx/configuration.md`](docs/sphinx/configuration.md) — config files, precedence, and FAQ
- [`docs/sphinx/reference.md`](docs/sphinx/reference.md) — operator-facing reference index
- [`docs/sphinx/recovery.md`](docs/sphinx/recovery.md) — failure classification, retry budgets, and recovery behavior
- [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md) — same-checkout parallel execution for multi-work-unit plans
- [`docs/sphinx/troubleshooting.md`](docs/sphinx/troubleshooting.md) — common issues and FAQ

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs.
Use it commercially. Use it privately. Use it however you want.
