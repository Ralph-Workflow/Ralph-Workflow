# Ralph Workflow (Python)

> An opinionated AI agent orchestration framework.

Ralph Workflow is a Python 3.12+ CLI and framework for configurable, opinionated AI agent orchestration. It began as a take on the Ralph loop, and the maintained package now turns that philosophy into configurable workflow, agent, and policy primitives. The installable package lives in this directory and exposes two entry points:

- `ralph` — the main CLI
- `ralph-mcp` — the standalone MCP server runtime

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### Development install

```bash
make dev
ralph --version
```

### Refresh the runnable executable from this checkout

```bash
make install
ralph --version
```

`make install` updates the current Python environment and, when `pipx` is
available, force-refreshes the pipx-managed `ralph` executable so it points at
the current checkout instead of a stale deleted worktree.

## Quick start

```bash
cd /path/to/your/project
ralph --init
# edit PROMPT.md
ralph
```

`ralph --init` is the canonical form. Compatibility labels such as `default` are deprecated, ignored, and no longer recommended in docs or scripts.

### First-run configuration

On first run, Ralph Workflow auto-creates seven config files from bundled, fully-commented templates:

**User-global (created once, reused across all projects):**
- `~/.config/ralph-workflow.toml` — main Ralph Workflow configuration
- `~/.config/ralph-workflow-mcp.toml` — MCP servers, web search, and web visit configuration

**Project-local (created by `ralph --init`, lives in your project directory):**
- `.agent/ralph-workflow.toml` — project-local main config override
- `.agent/mcp.toml` — project-local MCP override
- `.agent/agents.toml` — agent chain definitions and drain bindings
- `.agent/pipeline.toml` — phase graph and orchestration policy
- `.agent/artifacts.toml` — MCP artifact contracts per drain

**Override precedence (highest to lowest):**
CLI flags → project-local (`.agent/`) → user-global (`~/.config/`) → bundled defaults

These ship with sane defaults — you only need to edit them if you want to override something specific.

**Ralph Workflow init:** Run `ralph --init` to seed all project-local files.

**Regeneration:** To reset all configs from the bundled defaults (existing files are backed up to `<name>.bak`), run:

```bash
ralph --regenerate-config
```

The first-run welcome banner shows which files were created and checks whether your AI agents are on PATH.

The first-run panel now also:

- Displays the Ralph Workflow ASCII banner above the setup panel.
- Recommends running `ralph --diagnose` to validate agents, MCP servers, and config before the first pipeline run.
- Lists install URLs next to any known missing agent (`claude`, `opencode`) so you know where to get them.

### Upstream MCP HTTP endpoint compatibility

For custom upstream MCP servers in `.agent/mcp.toml`, Ralph Workflow now supports both current streamable HTTP endpoints and legacy HTTP+SSE endpoints under `transport = "http"`.

- Prefer `http://host:port/mcp` for modern streamable HTTP servers.
- Legacy endpoints such as docs-mcp `http://host:6280/sse` are also supported end to end.

This matters because `/sse` is not just a different path — it uses the older MCP HTTP+SSE flow, where Ralph Workflow must open an SSE stream first and then POST JSON-RPC messages to the advertised message endpoint.

## Documentation

The full reference documentation lives in `docs/sphinx/` and is published as the
`/docs` section of <https://ralphworkflow.com>. Build and browse it locally:

```bash
cd ralph-workflow
make docs          # build HTML into docs/sphinx/_build/html/
make serve-docs    # serve on http://localhost:8080
```

Key pages:

- [`docs/sphinx/getting-started.md`](docs/sphinx/getting-started.md) — step-by-step first-run walkthrough for new users
- [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md) — install, init, and run in five minutes
- [`docs/sphinx/concepts.md`](docs/sphinx/concepts.md) — terminology: phases, drains, agents, MCP artifacts, checkpoints
- [`docs/sphinx/reference.md`](docs/sphinx/reference.md) — operator-facing reference index
- [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md) — developer and maintainer docs index
- [`docs/sphinx/cli.md`](docs/sphinx/cli.md) — all CLI flags and sub-commands
- [`docs/sphinx/configuration.md`](docs/sphinx/configuration.md) — config files, precedence, and FAQ
- [`docs/sphinx/recovery.md`](docs/sphinx/recovery.md) — failure classification, retry budgets, and connectivity-aware recovery
- [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md) — parallel worktree execution for multi-work-unit plans
- [`docs/sphinx/troubleshooting.md`](docs/sphinx/troubleshooting.md) — common issues and FAQ
- [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst) — Python API reference (autodoc)

## Verification

```bash
make verify
```

That runs:

- `ruff check ralph/ tests/`
- `uv run python -m mypy ralph/`
- `uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going`
- `uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under 80`

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

- `review` must leave behind a fresh `.agent/artifacts/issues.json`.
- `development` and `fix` workers in isolated parallel runs are judged by empirical evidence: submitted artifacts are checked first; if none are found, workspace changes (untracked or modified files detected by `git status`) serve as the fallback signal. A worker is considered successful when either signal is present, and exit code is retained as diagnostic information only.
- Planning keeps `.agent/artifacts/plan.json` as the canonical machine-readable artifact and mirrors it to `.agent/PLAN.md` as the human/agent handoff.
- The runner still removes per-phase artifacts before each invocation so interrupted runs cannot leak stale summaries or review findings into later phases.

Artifact contract:
- Use `.json` artifacts for Ralph Workflow's validation, routing, checkpointing, and other orchestrator-only logic.
- Use `.md` handoff files when a user or downstream AI agent needs to read the result of an earlier phase.
- Current mirrored handoffs are:
  - `.agent/PLAN.md`
  - `.agent/DEVELOPMENT_RESULT.md`
  - `.agent/ISSUES.md`
  - `.agent/FIX_RESULT.md`
  - `.agent/DEVELOPMENT_ANALYSIS_DECISION.md`
  - `.agent/REVIEW_ANALYSIS_DECISION.md`

This hardening is intentionally selective. Review and planning still rely on explicit artifacts where Ralph Workflow needs structured evidence, while development and fix workers are judged by the empirical evidence they leave behind (artifacts and/or workspace changes), not by process exit code.

## Built-in web tools

### Web search (`web_search`)

Enabled by default. Uses a multi-backend fallback chain (ddgs, Tavily, Brave, Exa, SearXNG).
Configure via `[web_search]` in `mcp.toml`. Granted to 8 of 10 drains by default (not `analysis`, not `commit`).

### URL fetching (`visit_url`)

A built-in `visit_url` tool fetches a single HTTP/HTTPS page and returns readable extracted text.
Requires the optional extras:

```bash
pip install "ralph-workflow[web-visit]"
```

Granted to **all 10 session drains** by default. Configure via `[web_visit]` in `mcp.toml`.
See [`docs/mcp/web-visit.md`](docs/mcp/web-visit.md) for the full reference.

For multi-page or JavaScript-rendered crawling, wire in [Crawl4AI](https://docs.crawl4ai.com/)
as an upstream MCP server — see [`docs/mcp/mcp-servers.md`](docs/mcp/mcp-servers.md).

## Multimodal MCP Support (opt-in)

Ralph Workflow supports image-reading MCP tools via the `read_image` tool. This feature is **disabled by default** to maintain backward compatibility with text-only clients.

### Enabling Multimodal Support

Add a `[media]` section to `.agent/mcp.toml`:

```toml
[media]
enabled = true
max_inline_bytes = 5242880  # 5 MiB, default
```

### How read_image Works

When enabled, `read_image` is available with these characteristics:

- **Supported formats**: PNG, JPEG, GIF, WebP
- **Returns**: MCP image content block with base64-encoded data
- **Size guard**: Enforces `max_inline_bytes` limit (default 5 MiB)
- **Capability required**: `MediaRead` (granted by session policy)

### Compatibility Contract

The multimodal support is designed with strict backward compatibility:

1. **Text-only clients unchanged** — Existing tools (`read_file`, `write_file`, etc.) continue to return text content blocks with the same shape.
2. **Client capability filtering** — `read_image` only appears in `tools/list` for clients that declare multimodal/image/media capability in the MCP `initialize` handshake.
3. **Upstream multimodal rejection** — If an upstream MCP server returns a non-text content block, Ralph Workflow rejects it with a clear error rather than silently passing it through.

### What Text-Only Clients See

When a client connects without declaring multimodal support, `read_image` is **not visible** in `tools/list`, even if `media.enabled = true`. The text-only tool set is byte-equivalent to pre-multimodal behavior.

### What Multimodal Clients See

Clients that declare `capabilities.image`, `capabilities.media`, or `capabilities.multimodal` in the `initialize` request will see `read_image` in `tools/list` when `media.enabled = true`.

## Claude/CCS MCP Safety Note

Claude-compatible transports such as `claude` and `ccs` run through a stricter MCP path. Ralph Workflow still uses `--mcp-config` plus `--strict-mcp-config`, but it only emits `--tools ""` / `--allowedTools ...` when live MCP tool discovery succeeds with a non-empty allowlist. That avoids a brittle edge case in non-interactive Claude/CCS runs where empty-tool configurations and MCP bootstrapping can produce misleadingly successful no-op executions.

Ralph Workflow's Claude parser accepts both bare (`claude: ...`) and model-qualified (`claude/<model>: ...`) transcript prefixes emitted by the Claude CLI. Lifecycle-only markers (`message_delta`, `user`, `system (status=...)`, `thinking` without a payload) are automatically suppressed so they never appear as noise in the activity log. Free-form text and tool lines after the prefix are parsed normally.

## Parallel Mode

When your planning phase produces two or more work units, Ralph Workflow fans development out across multiple git worktrees simultaneously. For the full guide including configuration, work unit structure, and success criteria, see [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md).

Quick configuration:

```toml
[pipeline.parallel_execution]
max_parallel_workers = 4
max_work_units = 50
```

## Recovery

Ralph Workflow treats failure recovery as a first-class concern. The pipeline is designed to keep running through transient failures, preserve enough context to resume cleanly, and only terminate on user intent or pre-flight validation errors. For the full guide including failure categories, offline detection, the two-SIGINT contract, and configuration knobs, see [`docs/sphinx/recovery.md`](docs/sphinx/recovery.md).

## Transcript layout

Ralph Workflow emits every agent output line as a structured plain-text entry in the following format:

```
<ISO-TS> <LEVEL> <CAT> [<tag>][<unit>] <content>
```

**Levels** indicate severity and importance:

| Level | Meaning |
|-------|---------|
| `INFO` | Routine update or progress |
| `SUCCESS` | Phase or pipeline completed successfully |
| `WARN` | Non-fatal issue or degraded state |
| `ERROR` | Fatal error or malformed input |
| `MILESTONE` | Major phase transition (planning, development, review, fix) |

#### Level badges (TTY only)

When stderr is a TTY, level tokens are rendered in themed colors using the Okabe-Ito palette (blue for INFO, bold green for SUCCESS, bold orange for WARN, bold red for ERROR, bold sky-blue for MILESTONE). Under `NO_COLOR` or when piping to a file the tokens remain plain uppercase text — copy-paste safe.

**Categories** (`CAT`) group tags into two buckets:

| Category | Meaning |
|----------|---------|
| `META` | Workflow metadata: phase, plan, activity, worker, result, etc. |
| `CONT` | Agent-produced content: text, thinking, tool calls, errors |

**Tags** indicate the source and type of the line:

| Tag | Category | Meaning |
|-----|----------|---------|
| `phase` | META | Workflow phase transition |
| `phase-close` | META | Compact single-line recap with elapsed time and per-phase counters |
| `plan` | META | Plan summary or scope |
| `plan-scope` | META | Plan scope items |
| `plan-steps` | META | Step progress |
| `activity` | META | Agent activity snapshot: free-form activity line from the agent when available, otherwise structured key=value fields (tool, path, workdir) |
| `analysis` | META | Phase analysis and decision |
| `worker` | META | Parallel worker status update |
| `result` | META | Pipeline completion result |
| `pr` | META | Pull request URL |
| `artifact` | META | Artifact kind/summary |
| `progress` | META | Progress update |
| `run-start` | META | One-time pipeline orientation emitted at run start |
| `run-end` | META | One-time pipeline close emitted at run end |
| `content` | CONT | Agent text output (one-shot, non-streaming) |
| `content-start` | CONT | Start of a streaming text block |
| `content-continue` | CONT | Continuation line in a streaming text block |
| `content-end` | CONT | End of a streaming text block (with headline summary) |
| `content-checkpoint` | CONT | Mid-stream orientation line emitted every 20 fragments or 4000 chars |
| `thinking` | CONT | Agent thinking/reasoning (one-shot) |
| `thinking-start` | CONT | Start of a streaming thinking block |
| `thinking-continue` | CONT | Continuation of a streaming thinking block |
| `thinking-end` | CONT | End of a streaming thinking block |
| `thinking-checkpoint` | CONT | Mid-stream orientation line for thinking blocks |
| `tool` | CONT | Tool invocation (tool name) |
| `tool-result` | CONT | Tool result content |
| `error` | CONT | Error or malformed input |
| `status-content` | CONT | Status or lifecycle event from the agent |

**Streaming blocks**: consecutive `text` or `thinking` activity lines from the same worker are grouped into `start`/`continue`/`end` sequences so progressive output feels coherent. When a different kind or a lifecycle event arrives, the open block is automatically closed with a `content-end` (or `thinking-end`) line whose content is a one-line headline summary of the accumulated block.

**Thinking previews**: Thinking blocks emit a `↳ preview:` line on open, on each checkpoint, and on close so you can see what the agent is reasoning about without waiting for the block to finish.

**Tool result summaries**: Tool results with substantial content (>=80 characters) emit a `↳ summary:` line above the result so you can see what the tool returned at a glance.

**META [activity] deduplication**: When a `[tool]` CONT line has already been emitted for a unit, the following structured `[activity]` META line is suppressed to avoid redundant noise. The free-form activity line path is unaffected.

**Oversized content** is condensed to a head+tail excerpt with a pointer:

```
2026-04-20T12:34:56Z INFO CONT [content][dev-1] AAAAAAA … (+4200 chars, see .agent/raw/dev-1.log) … ZZZZZZZ
```

When a content block exceeds the soft limit and is condensed, the full text is preserved to `.agent/raw/<unit-id>.log` so you can inspect the complete output. Malformed input lines that cannot be parsed are also preserved there for diagnosis. Short, non-condensed output is not written to the raw log.

### Reading a transcript at a glance

```
2026-04-21T12:00:00+00:00 MILESTONE META [run-start] ◆ Ralph Workflow run start
2026-04-21T12:00:00+00:00 INFO META [run-start] legend: LEVEL (INFO/SUCCESS/WARN/ERROR/MILESTONE)  CAT (META/CONT)  [tag][unit] message
2026-04-21T12:00:00+00:00 INFO META [run-start] prompt=PROMPT.md
2026-04-21T12:00:00+00:00 INFO META [run-start] developer=claude model=claude-3-5-sonnet
2026-04-21T12:00:00+00:00 INFO META [run-start] reviewer=claude model=claude-3-5-haiku
2026-04-21T12:00:00+00:00 INFO META [run-start] iterations=dev:3 reviewer:1
2026-04-21T12:00:00+00:00 INFO META [run-start] plan=ready
2026-04-21T12:00:00+00:00 INFO META [run-start] verbosity=verbose
2026-04-21T12:00:00+00:00 INFO META [run-start] workspace=/workspace
2026-04-21T12:00:00+00:00 MILESTONE META [phase] ◆ development
2026-04-21T12:00:01+00:00 INFO META [plan] (no plan loaded yet)
2026-04-21T12:00:02+00:00 INFO CONT [tool][dev-1] mcp__ralph__read_file (path=ralph-workflow/ralph/x.py)
2026-04-21T12:00:03+00:00 INFO CONT [thinking-start][dev-1] ↳ preview: Let me investigate the codebase first.
2026-04-21T12:00:04+00:00 INFO CONT [content-start][dev-1] Refactored parser to accept streaming deltas
2026-04-21T12:00:05+00:00 INFO CONT [content-continue#2][dev-1] next chunk
2026-04-21T12:00:06+00:00 INFO CONT [content-end][dev-1] (2 fragments, 850 chars) Refactored parser to accept streaming deltas
2026-04-21T12:00:06+00:00 INFO CONT [thinking-end][dev-1] (1 fragments, 45 chars) Let me investigate the codebase first.
2026-04-21T12:00:06+00:00 INFO CONT [thinking-end][dev-1] ↳ preview: Let me investigate the codebase first.
2026-04-21T12:00:07+00:00 SUCCESS CONT [tool-result][dev-1] ↳ summary: # Template Registry...
2026-04-21T12:00:07+00:00 SUCCESS CONT [tool-result][dev-1] # Template Registry\n\nThis module manages...
2026-04-21T12:00:08+00:00 INFO META [phase-close] phase=development development: result artifact present (elapsed=12.4s, content_blocks=2, thinking_blocks=1, tool_calls=3, errors=0)
```

The `[run-start]` block is emitted once at pipeline start , the `[phase-close]` line is emitted once at the end of each phase's artifact rendering, and the `[run-end]` block is emitted once at pipeline stop; all three are suppressed when running with `--quiet`.

Tags starting with `content-`, `thinking-`, `tool`, `tool-result`, `error`, or `status-content` are CONT (agent-produced); everything else is META (workflow). Streaming blocks are always closed with a `-end` line before a different unit or a different kind is emitted. A `↳ summary:` line preceding condensed content is an additional, deterministic headline layer — not a replacement for the content itself; the full text is always available at `.agent/raw/<unit>.log`. Similarly, `↳ preview:` lines for thinking blocks surface the reasoning headline at open, checkpoint, and close.

## Long-content display

Ralph Workflow applies three distinct layers when agent content is large. Each layer is additive — earlier layers remain active when later layers are enabled.

### Layer 1 — condensation (always active)

Condensation is the deterministic default for oversized lines and is always active. Ralph Workflow applies two tiers based on the total display-cell count:

- **400–4000 cells (soft limit)** — head-only truncation. The first 400 cells are kept and a `(truncated)` suffix is appended:

  ```
  2026-04-20T12:34:56Z INFO CONT [content][dev-1] First 400 chars… (truncated, see .agent/raw/dev-1.log)
  ```

- **> 4000 cells (hard limit)** — head+tail truncation with the middle elided:

  ```
  2026-04-20T12:34:56Z INFO CONT [content][dev-1] First 2000 chars… (+8400 chars, see .agent/raw/dev-1.log) …last 2000 chars
  ```

The full raw text is always preserved to `.agent/raw/<unit-id>.log` so readers have a path to the complete output.
### Layer 2 — deterministic headline `↳ summary:` (default-on)

For content blocks exceeding 4000 display cells, Ralph Workflow emits a `↳ summary:` line **before** the condensed excerpt. This deterministic headline layer is **default-on** — no environment variable is needed:

```
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ summary: My first non-empty headline sentence.
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] First 400 chars… (+4200 chars, see .agent/raw/dev-1.log) …last chars
```

When no extractable headline exists (all lines are blank, markdown-only, or empty after stripping), the placeholder `(no headline available)` is emitted instead so the summary line is never silently dropped for oversized content.

For tool results with substantial content (>=80 characters) that are below the 4000-cell condensation threshold, Ralph Workflow also emits a `↳ summary:` line using the same headline extraction logic, giving you a preview without requiring condensation.

To **disable** the summary layer, set `RALPH_LONG_CONTENT_SUMMARY` to one of: `0`, `false`, `no`, `off` (case-insensitive):

```bash
export RALPH_LONG_CONTENT_SUMMARY=0  # disable the summary layer
```

Any other value — including an unset variable — leaves the summary enabled.

Inline summaries are truncated at 200 characters. Streaming end-line summaries inside `[content-end]`/`[thinking-end]` lines are truncated at 120 characters.

The headline is deterministic: the first non-empty line with markdown heading and quote prefixes stripped, terminated at the first sentence boundary (`.`, `!`, `?`, or newline). No external AI call is made.

### Layer 3 — optional AI summary `↳ ai-summary:` (default-OFF)

An optional AI-generated summary can be emitted after the deterministic headline. This layer is **disabled by default** and requires two things:

1. Set `RALPH_LONG_CONTENT_AI_SUMMARY=1` (or `true`/`yes`).
2. Register a hook via `ralph.display.long_content_summary.set_ai_summary_hook(callable)`.

When both conditions are met and content exceeds the 4000-cell threshold, Ralph Workflow calls the hook with the raw text and emits the result on its own labelled line:

```
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ summary: My first sentence.
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ ai-summary: Higher-level recap produced by the hook.
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] First 400 chars… (+4200 chars, see .agent/raw/dev-1.log) …last chars
```

Hook requirements: the callable must accept `(text: str) -> str | None`. Exceptions are swallowed and treated as None. Output is capped at 400 characters. Ralph Workflow provides no built-in hook — integrators supply their own. The hook must be non-blocking.

### Mid-stream checkpoints (default-on)

For very long streaming blocks, Ralph Workflow emits a `[content-checkpoint#N]` orientation line every 20 fragments or every 4000 accumulated characters (whichever comes first), with a running fragment count, char total, and deterministic headline:

```
2026-04-20T12:34:56Z INFO CONT [content-checkpoint#20][dev-1] (20 fragments, 4500 chars) My running headline.
```

For thinking blocks, a `↳ preview:` line is also emitted at each checkpoint so you can see the accumulated reasoning so far:

```
2026-04-20T12:34:56Z INFO CONT [thinking-checkpoint#20][dev-1] (20 fragments, 4500 chars) Investigating the codebase structure...
2026-04-20T12:34:56Z INFO CONT [thinking-checkpoint#20][dev-1] ↳ preview: Investigating the codebase structure...
```


### Example [run-end] block

```
2026-04-21T12:05:00+00:00 MILESTONE META [run-end] ◆ Ralph Workflow run end
2026-04-21T12:05:00+00:00 INFO META [run-end] phase=complete
2026-04-21T12:05:00+00:00 INFO META [run-end] elapsed=42.3s
2026-04-21T12:05:00+00:00 INFO META [run-end] content_blocks=12
2026-04-21T12:05:00+00:00 INFO META [run-end] thinking_blocks=4
2026-04-21T12:05:00+00:00 INFO META [run-end] tool_calls=28
2026-04-21T12:05:00+00:00 INFO META [run-end] errors=0
2026-04-21T12:05:00+00:00 INFO META [run-end] agent_calls=7
2026-04-21T12:05:00+00:00 INFO META [run-end] pr=https://github.com/test/repo/pull/123
```


### Pipeline Complete / Pipeline Failed panel

For terminal phases (`complete` and `failed`), Ralph Workflow prints a Rich summary panel
immediately after the `[run-end]` block. The panel title is **Pipeline Complete**
or **Pipeline Failed** and echoes the plan, decision log, metrics,
verification status, commit, PR URL, and open risks seen during the run.
Non-terminal phases (e.g. `development`, `review`) do not produce a panel.

The panel includes an **Activity Summary** section that mirrors the `[run-end]` block counters:
`elapsed`, `content_blocks`, `thinking_blocks`, `tool_calls`, `errors`, and `agent_calls`.
The `raw_overflow` path is shown when an overflow log was written.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `RALPH_STREAMING_DEDUP` | `1` | Set to `0` to disable identical-consecutive-fragment suppression |
| `RALPH_STREAMING_CHECKPOINTS` | `1` | Set to `0` to disable mid-stream checkpoint lines |
| `RALPH_LONG_CONTENT_SUMMARY` | `1` | Set to `0` to disable the deterministic headline summary layer |
| `RALPH_LONG_CONTENT_AI_SUMMARY` | `0` | Set to `1` to enable the optional AI summary layer (requires hook registration) |
| `NO_COLOR` | unset | Disable all ANSI color output; level/category badges remain plain text. Honored by `make_console`. |
| `FORCE_COLOR` | unset | Force ANSI color output even when not a TTY. Honored by `make_console`. |
To disable checkpoints, set `RALPH_STREAMING_CHECKPOINTS=0`.
