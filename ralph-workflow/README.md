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
| `.agent/pipeline.toml` | Phase graph, roles, transitions, retry rules, analysis loops, artifact requirements, commit semantics, recovery routing, parallel execution |
| `.agent/ralph-workflow.toml` | Agent chains and drain-to-chain bindings |
| `.agent/artifacts.toml` | Artifact contracts, paths, and decision vocabularies per drain |

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
- Phase-owned artifact requirements

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
# edit PROMPT.md and remove the starter sentinel
ralph --diagnose
ralph
```

`ralph --init` is the canonical form. Compatibility labels such as `default` are deprecated,
ignored, and no longer recommended in docs or scripts. `ralph --init` scaffolds the project-local
support files from the user-global config set and seeds a small default `.gitignore` policy for Ralph Workflow
local artifacts such as `.agent/`, the local `PROMPT` file pattern, and default `wt-*`
worktree directories; use `ralph --init-local-config` only when this repo needs a full project-local
copy of the user-global config set instead of inheriting those values.

## First-run configuration

On first run, Ralph Workflow creates the standard project and user config files from bundled templates.

**User-global (created once, reused across projects):**
- `~/.config/ralph-workflow.toml` — main Ralph Workflow configuration
- `~/.config/ralph-workflow-mcp.toml` — MCP servers, web search, and web visit configuration
- `~/.config/ralph-workflow-pipeline.toml` — user-global pipeline defaults for new workspaces and no-local-override runs
- `~/.config/ralph-workflow-artifacts.toml` — user-global artifact-contract defaults for new workspaces and no-local-override runs

**Project-local support files (created by `ralph --init`, live in your project directory):**
- `.agent/mcp.toml` — project-local MCP override copied from the user-global MCP config when present
- `.agent/pipeline.toml` — project-local phase graph copied from the user-global pipeline config when present
- `.agent/artifacts.toml` — project-local artifact contracts copied from the user-global artifacts config when present

**Optional full project-local override copy (created only when you ask for it):**
- `.agent/ralph-workflow.toml` — project-specific main config override, including agent chains and drain bindings
- `ralph --init-local-config` also refreshes `.agent/mcp.toml`, `.agent/pipeline.toml`, and `.agent/artifacts.toml` from the user-global config set when they are missing

**Override precedence (highest to lowest):**
CLI flags → project-local (`.agent/`) → user-global (`~/.config/`) → bundled defaults

To reset configs from the bundled defaults (existing files are backed up to `<name>.bak`), run:

```bash
ralph --regenerate-config
```

Before your first real run, use the recommended verification step:

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
- `make test-cov` — runs pytest with coverage enabled and enforces ≥80% coverage (uses `$(PYTEST_WORKERS)` workers, defaulting to 8; excludes subprocess_e2e tests)
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

Ralph Workflow has broad multimodal support via `read_media` (primary tool) and `read_image` (compatibility alias). This feature is **enabled by default**.

Supported modalities include images (PNG, JPEG, GIF, WebP), PDFs, documents, audio, video, and resource/file-reference-based flows. At session start, Ralph Workflow resolves a capability profile for the active provider and model, capturing supported modalities, delivery mode, typed-block form, and explicit unsupported reasons as a single runtime-owned contract. This profile is persisted in the session payload and consumed by all downstream tool handlers — no layer re-derives delivery decisions independently.

Delivery behavior per provider:

- **Claude/Anthropic** — images delivered inline; PDFs and documents delivered as typed blocks; audio and video are unsupported via Ralph Workflow's managed MCP path.
- **OpenAI/Codex** — vision-capable models (gpt-4o, gpt-4-turbo, o1, o3) receive images inline; PDFs, documents, audio, and video are explicitly unsupported via the chat completion API.
- **Gemini** — images delivered inline; PDFs, documents, audio, and video delivered as typed blocks.
- **Unknown providers** — all modalities are made available as replayable resource references (safe default, no capability blocked).

To disable multimodal support, add to `.agent/mcp.toml`:

```toml
[media]
enabled = false
```

When enabled (default):
- `read_media` exposes broad multimodal capability; `read_image` is a compatibility alias for inline-image workflows
- multimodal tools only appear for clients that declare multimodal/image/media capability in `initialize`
- text-only clients keep the pre-multimodal tool set unchanged
- inline delivery (base64-encoded data blocks) is used when the model supports it; resource-reference delivery is used otherwise
- `max_inline_bytes` enforces the inline size guard (5 MiB by default)
- upstream non-text content blocks are normalized to `resource_reference` artifacts rather than rejected

To customize, add to `.agent/mcp.toml`:

```toml
[media]
enabled = true  # (default, can be omitted)
max_inline_bytes = 10485760  # 10 MiB to allow larger images
```

### Compatibility contract

The multimodal support is designed with strict backward compatibility:

1. **Text-only clients unchanged** — Existing tools (`read_file`, `write_file`, etc.) continue to return text content blocks with the same shape.
2. **Client capability filtering** — `read_media` and `read_image` only appear in `tools/list` for clients that declare multimodal/image/media capability in the MCP `initialize` handshake.
3. **Upstream normalization** — If an upstream MCP server returns a non-text content block, Ralph Workflow normalizes it to a `resource_reference` artifact (for embedded-data content) or preserves the external URI (for URI-backed content) rather than rejecting it or silently dropping it.

### What text-only clients see

When a client connects without declaring multimodal support, `read_media` and `read_image` are **not visible** in `tools/list`, even if `media.enabled = true`. The text-only tool set is byte-equivalent to pre-multimodal behavior.

### What multimodal clients see

Clients that declare `capabilities.image`, `capabilities.media`, or `capabilities.multimodal` in the `initialize` request will see `read_media` and `read_image` in `tools/list` when `media.enabled = true`.

### Supported multimodal workflows

Ralph Workflow supports the following first-class multimodal workflow patterns:

- **Screenshot and browser-captured visual QA** — a browser automation tool captures a screenshot; Ralph Workflow preserves it as multimodal context, routes it to the model inline (for capable providers) or as a replayable `ralph://media/<id>` artifact retrievable via `resources/read`.
- **Mixed-modality execution** — workflows that combine multiple modalities in a single run (e.g. screenshot + PDF context, audio + text artifacts, image + document metadata). Ralph Workflow treats these as normal platform use cases rather than edge cases.
- **Replayable resource handles** — for providers that do not support inline delivery, or when the artifact should remain accessible after the initial call, Ralph Workflow stores the bytes in the session manifest and returns a `ralph://media/<id>` URI. The artifact bytes are retrievable via `resources/read` using the same URI, allowing replay across tool calls.
- **Document understanding** — PDFs and office documents where layout or visual structure matters are delivered as typed blocks (Claude, Gemini) or as replayable resource references (unknown providers), preserving structure rather than collapsing to plain text.
- **Audio and video understanding** — audio/video modalities are delivered as typed blocks for Gemini; Ralph Workflow returns an explicit unsupported error for providers that do not support those modalities, rather than silently degrading.

## MCP server robustness

Ralph Workflow runs external MCP servers (declared in `mcp.toml`) as subprocesses inside `RestartAwareMcpBridge`. Each bridge wraps a `StandaloneMcpProcess` and monitors it for unexpected exits:

- **Preflight on every spawn** — before the bridge becomes active (initial start and each restart), a preflight probe connects to the server's HTTP endpoint and verifies that all required tools are advertised. A missing tool on restart is treated the same as on initial startup and surfaces a hard error.
- **Bounded auto-restart** — if the MCP server becomes unhealthy during a pipeline run, the bridge restarts it automatically up to `McpRestartPolicy.max_restarts` times (default 1000). When the budget is exhausted, `McpServerError` is raised and the pipeline surfaces the restart count so the failure is diagnosable.
- **Responsiveness probe** — the bridge detects not only crashed processes but also alive-but-wedged ones. On every supervision cycle, `probe_mcp_http_endpoint` sends an isolated `initialize` → `notifications/initialized` → `tools/list` JSON-RPC handshake using a **fresh, independent MCP session** (never reusing the agent's active session). If the server does not respond within the probe timeout (default 5 s, configurable via `RALPH_MCP_PROBE_TIMEOUT_MS`), it is treated as unhealthy and restarted.
- **Stable endpoint** — the port is reserved once when `start_mcp_server()` creates the bridge and reused on every restart, so `MCP_ENDPOINT_ENV` never changes for an already-running agent after a mid-run crash.
- **Active supervision during execution** — `McpSupervisor` (in `ralph.process.mcp_supervisor`) polls `check_mcp_bridge_health(bridge)` in a background thread for the entire duration of each agent attempt, not just at retry boundaries. Both crashed and hung-but-alive servers are detected within the supervision interval (default 2 s) instead of surfacing as opaque `MCP error -32001: Request timed out` failures.
- **Display breadcrumb** — when at least one restart has occurred, the restart count is forwarded to `PipelineSubscriber.record_mcp_restart()` and surfaced as `mcp_restarts: <n>` in the debug output at run completion.

All process spawning for both MCP servers and AI agents flows through `ProcessManager` in `ralph/process/`. Do not spawn subprocesses outside this class.

## Claude/CCS MCP safety note

Claude-compatible transports such as `claude` and `ccs` run through a stricter MCP path. Ralph Workflow still uses `--mcp-config` plus `--strict-mcp-config`, but it only emits `--tools ""` / `--allowedTools ...` when live MCP tool discovery succeeds with a non-empty allowlist. That avoids a brittle edge case in non-interactive Claude/CCS runs where empty-tool configurations and MCP bootstrapping can produce misleadingly successful no-op executions.

Ralph Workflow's Claude parser accepts both bare (`claude: ...`) and model-qualified (`claude/<model>: ...`) transcript prefixes emitted by the Claude CLI. Lifecycle-only markers (`message_delta`, `user`, `system (status=...)`, `thinking` without a payload) are automatically suppressed so they never appear as noise in the activity log. Free-form text and tool lines after the prefix are parsed normally.

## Parallel mode

When the planning phase produces two or more work units, Ralph Workflow runs them as parallel workers in the **same git checkout** (same-workspace mode v1). Each worker is restricted to its declared `allowed_directories` and writes its artifacts under `.agent/workers/<unit_id>/`. Workers share the checkout and write to it directly, without separate git branches; coordination uses edit-area fencing and artifact namespaces only.

**Multimodal session contract** — Same-workspace workers inherit the parent phase's `SessionMcpPlan` contract verbatim, including the resolved capability profile, model identity, and drain. This means parallel workers expose the same multimodal capability surface as serial execution: `read_media` and `read_image` are available by default when the parent phase has `media.read` capability, and delivery verdicts (inline image, typed block, resource reference replay, explicit unsupported) are provider-specific and consistent with the serial path. Worker-produced media artifacts are written under the worker's namespace with the phase-scoped handoff path, not a standalone fallback. For the full guide including configuration, work unit structure, and success criteria, see [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md).

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
In **medium mode**, a blank line precedes the banner to provide a visual boundary between phases.
In **wide mode**, a titled Rule separator precedes the banner; its title is the phase label plus
any iteration context (`Development Analysis  ◎ Dev 2/5  ▸ Analysis 3/5`), making the heading
immediately scannable.  The agent name appears on its own indented line.

**Phase-close banners** are symmetric with phase-start: same field ordering and glyphs, followed
by elapsed time (e.g. `7.5s`) and the exit trigger (e.g. `→ produced`, `→ completed`). In
**medium and wide mode**, two additional lines appear below the header when applicable:
- `↳ stats:` — content/thinking/tool/error activity counters (omitted when all are zero)
- `↳ artifact:` — what the phase produced (e.g. `plan: 5 step(s), 2 risk(s)`)

For review phases, a `review: clean` or `review: issues found` line is always shown regardless
of display mode. When a phase ends with debug breadcrumbs still set (waiting-status or failure
category), a `debug:` line is appended immediately below the close banner in all display modes —
making failure context visible without reading the completion summary.

In **wide mode**, a titled trailing Rule is printed after the close banner.  Its title shows the
elapsed time and exit trigger (`7.5s  → produced`) so the section footer mirrors the header and is
immediately readable when scrolling through output.  When neither elapsed time nor exit trigger is
available, a plain Rule is printed instead.  This trailing Rule symmetrically closes the section
opened by the phase-start titled Rule.

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
- [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md) — contributor and integrator index (architecture, internals, API)
- [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst) — autodoc Python API reference for all public `ralph.*` packages and modules

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs.
Use it commercially. Use it privately. Use it however you want.
