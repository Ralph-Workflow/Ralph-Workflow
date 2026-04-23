# Ralph Workflow (Python)

Ralph Workflow is a Python 3.12+ CLI for unattended multi-agent development loops. The installable package lives in this directory and exposes two entry points:

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
ralph --init feature-spec
# edit PROMPT.md
ralph
```

### First-run configuration

On first run, Ralph auto-creates seven config files from bundled, fully-commented templates:

**User-global (created once, reused across all projects):**
- `~/.config/ralph-workflow.toml` — main Ralph configuration
- `~/.config/ralph-workflow-mcp.toml` — MCP servers and web search configuration

**Project-local (created by `ralph --init`, lives in your project directory):**
- `.agent/ralph-workflow.toml` — project-local main config override
- `.agent/mcp.toml` — project-local MCP override
- `.agent/agents.toml` — agent chain definitions and drain bindings
- `.agent/pipeline.toml` — phase graph and orchestration policy
- `.agent/artifacts.toml` — MCP artifact contracts per drain

**Override precedence (highest to lowest):**
CLI flags → project-local (`.agent/`) → user-global (`~/.config/`) → bundled defaults

These ship with sane defaults — you only need to edit them if you want to override something specific.

**Ralph init:** Run `ralph --init` to seed all project-local files.

**Regeneration:** To reset all configs from the bundled defaults (existing files are backed up to `<name>.bak`), run:

```bash
ralph --regenerate-config
```

The first-run welcome banner shows which files were created and checks whether your AI agents are on PATH.

## Verification

```bash
make verify
```

That runs:

- `ruff check ralph/ tests/`
- `uv run python -m mypy ralph/`
- `uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under=80`

For narrower local runs, use:

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

Ralph now treats several agent-driven phases as producing explicit evidence, not just a zero exit code.

- `review` must leave behind a fresh `.agent/artifacts/issues.json`.
- `development` and `fix` workers in isolated parallel runs are judged by empirical evidence: submitted artifacts are checked first; if none are found, workspace changes (untracked or modified files detected by `git status`) serve as the fallback signal. A worker is considered successful when either signal is present, and exit code is retained as diagnostic information only.
- Planning keeps `.agent/artifacts/plan.json` as the canonical machine-readable artifact and mirrors it to `.agent/PLAN.md` as the human/agent handoff.
- The runner still removes per-phase artifacts before each invocation so interrupted runs cannot leak stale summaries or review findings into later phases.

Artifact contract:
- Use `.json` artifacts for Ralph's validation, routing, checkpointing, and other orchestrator-only logic.
- Use `.md` handoff files when a user or downstream AI agent needs to read the result of an earlier phase.
- Current mirrored handoffs are:
  - `.agent/PLAN.md`
  - `.agent/DEVELOPMENT_RESULT.md`
  - `.agent/ISSUES.md`
  - `.agent/FIX_RESULT.md`
  - `.agent/DEVELOPMENT_ANALYSIS_DECISION.md`
  - `.agent/REVIEW_ANALYSIS_DECISION.md`

This hardening is intentionally selective. Review and planning still rely on explicit artifacts where Ralph needs structured evidence, while development and fix workers are judged by the empirical evidence they leave behind (artifacts and/or workspace changes), not by process exit code.

## Multimodal MCP Support (opt-in)

Ralph supports image-reading MCP tools via the `read_image` tool. This feature is **disabled by default** to maintain backward compatibility with text-only clients.

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
3. **Upstream multimodal rejection** — If an upstream MCP server returns a non-text content block, Ralph rejects it with a clear error rather than silently passing it through.

### What Text-Only Clients See

When a client connects without declaring multimodal support, `read_image` is **not visible** in `tools/list`, even if `media.enabled = true`. The text-only tool set is byte-equivalent to pre-multimodal behavior.

### What Multimodal Clients See

Clients that declare `capabilities.image`, `capabilities.media`, or `capabilities.multimodal` in the `initialize` request will see `read_image` in `tools/list` when `media.enabled = true`.

## Claude/CCS MCP Safety Note

Claude-compatible transports such as `claude` and `ccs` run through a stricter MCP path. Ralph still uses `--mcp-config` plus `--strict-mcp-config`, but it only emits `--tools ""` / `--allowedTools ...` when live MCP tool discovery succeeds with a non-empty allowlist. That avoids a brittle edge case in non-interactive Claude/CCS runs where empty-tool configurations and MCP bootstrapping can produce misleadingly successful no-op executions.

## Parallel Mode

When your planning phase produces two or more work units, Ralph fans development out across multiple git worktrees simultaneously. Configure it in your pipeline policy:

```toml
[pipeline.parallel_execution]
max_parallel_workers = 4
max_work_units = 50
```

See `docs/agents/parallelization.md` for the full guide.

## Recovery

Ralph treats failure recovery as a first-class concern. The pipeline is designed to keep running through transient failures, preserve enough context to resume cleanly, and only terminate on user intent or pre-flight validation errors.

### Failure categories

Every failure is classified into one of four categories:

| Category | Description | Counts against budget? |
|---------|-------------|----------------------|
| `environmental` | Network outage, upstream service error, transport disconnect | No — retries are free |
| `agent` | Empty output, idle timeout, malformed tool calls, repeated policy violations | Yes |
| `user_config` | Invalid config, unbound agent chain, missing required inputs | No — pre-flight should catch these |
| `ambiguous` | Cannot determine cause | No — flagged for review, counted in recovery cycles |

Attribution is intelligent: a re-prompt caused by a brief outage does not cost the agent a life; an empty-output timeout does. Ambiguous errors default to the safer retry path.

### Offline detection and auto-resume

Ralph actively monitors connectivity. While offline, the pipeline pauses — it makes no progress rather than burning budget or failing noisily. Once connectivity returns, the pipeline resumes automatically and re-prompts the affected iteration without counting the outage against any agent. You will see:

```
Offline — paused (since HH:MM:SS)
```

When connectivity is restored:

```
Recovery resumed after offline
```

### Two-SIGINT contract

- **First Ctrl+C**: cancels in-flight work, triggers ordered shutdown (kills subprocesses, saves checkpoint), then pauses. The pipeline can be resumed.
- **Second Ctrl+C**: exits immediately with no cleanup.

### Recovery-cycle cap

A global `recovery_cycle_cap` (default: 200) bounds the total number of full-chain exhaustion recovery cycles. When exceeded, the pipeline exits with a descriptive error referencing the cap value and the last failure. This prevents a persistently-failing handler from looping silently forever.

### Agent chain fallover

Each phase uses an agent chain (e.g., `claude → opencode`). When an agent exhausts its `max_retries` budget, Ralph falls over to the next agent in the chain with a clean state — no silent retries, no double-counting. Chain composition is validated pre-flight.

### How to read failure events in logs

Failure events are emitted as structured log entries with `recovery=true`:

```
2026-04-21 12:00:00 | DEBUG    | ralph.recovery | category=environmental phase=development agent=claude counted=False
2026-04-21 12:00:05 | INFO     | ralph.recovery | category=agent phase=development agent=claude counted=True
2026-04-21 12:00:10 | DEBUG    | ralph.recovery | category=fallover phase=development from_agent=claude to_agent=opencode
```

### Configuration knobs

```toml
[agents]
# Per-chain retry budget and backoff
[agents.chains.development]
agents = ["claude", "opencode"]
max_retries = 3          # per-agent retry budget
retry_delay_ms = 1000    # base delay before retry (exponential backoff, capped at 30s)

# Global recovery cycle cap (default: 200)
[pipeline]
recovery_cycle_cap = 200
```

**`retry_delay_ms`** controls the base delay between retries for agent-attributable failures. The delay uses exponential backoff: each retry doubles the delay (base_ms × 2^attempt), capped at 30 seconds. For example, with `retry_delay_ms = 1000`:
- Retry 1: 1 s delay
- Retry 2: 2 s delay
- Retry 3: 4 s delay
- Subsequent retries: capped at 30 s

Environmental and ambiguous failures always retry with 0 delay (immediately). The delay resets to base after a successful agent invocation or a chain fallover to the next agent.

Connectivity probe interval can be configured in code via `ConnectivityMonitor(probe_interval_s=10.0)`.

## Transcript layout

Ralph emits every agent output line as a structured plain-text entry in the following format:

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
| `activity` | META | Agent activity metadata (tool, path, workdir) |
| `activity-line` | META | Last raw activity line from an agent |
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

**Oversized content** is condensed to a head+tail excerpt with a pointer:

```
2026-04-20T12:34:56Z INFO CONT [content][dev-1] AAAAAAA … (+4200 chars, see .agent/raw/dev-1.log) … ZZZZZZZ
```

When a content block exceeds the soft limit and is condensed, the full text is preserved to `.agent/raw/<unit-id>.log` so you can inspect the complete output. Malformed input lines that cannot be parsed are also preserved there for diagnosis. Short, non-condensed output is not written to the raw log.

### Reading a transcript at a glance

```
2026-04-21T12:00:00+00:00 MILESTONE META [run-start] ◆ Ralph run start
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
2026-04-21T12:00:02+00:00 INFO META [activity] agent=claude tool=bash
2026-04-21T12:00:03+00:00 INFO CONT [content-start][dev-1] Refactored parser to accept streaming deltas
2026-04-21T12:00:04+00:00 INFO CONT [content-continue#2][dev-1] next chunk
2026-04-21T12:00:05+00:00 INFO CONT [content-end][dev-1] (2 fragments, 850 chars) Refactored parser to accept streaming deltas
2026-04-21T12:00:05+00:00 INFO META [phase-close] phase=development development: result artifact present (elapsed=12.4s, content_blocks=2, thinking_blocks=1, tool_calls=3, errors=0)
2026-04-21T12:00:06+00:00 INFO CONT [thinking-start][dev-1] I need to check the tests before…
2026-04-21T12:00:07+00:00 SUCCESS CONT [tool-result][dev-1] ok
2026-04-21T12:00:08+00:00 WARN META [progress][dev-1] dropped 3 lines since last flush
2026-04-21T12:00:09+00:00 INFO CONT [content][dev-1] AAAAA… (+4200 chars, see .agent/raw/dev-1.log) …ZZZZZZZ
```

The `[run-start]` block is emitted once at pipeline start , the `[phase-close]` line is emitted once at the end of each phase's artifact rendering, and the `[run-end]` block is emitted once at pipeline stop; all three are suppressed when running with `--quiet`.

Tags starting with `content-`, `thinking-`, `tool`, `tool-result`, `error`, or `status-content` are CONT (agent-produced); everything else is META (workflow). Streaming blocks are always closed with a `-end` line before a different unit or a different kind is emitted. A `↳ summary:` line preceding condensed content is an additional, deterministic headline layer — not a replacement for the content itself; the full text is always available at `.agent/raw/<unit>.log`.

## Long-content display

Ralph applies three distinct layers when agent content is large. Each layer is additive — earlier layers remain active when later layers are enabled.

### Layer 1 — condensation (always active)

Condensation is the deterministic default for oversized lines and is always active. Ralph applies two tiers based on the total display-cell count:

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

For content blocks exceeding 4000 display cells, Ralph emits a `↳ summary:` line **before** the condensed excerpt. This deterministic headline layer is **default-on** — no environment variable is needed:

```
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ summary: My first non-empty headline sentence.
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] First 400 chars… (+4200 chars, see .agent/raw/dev-1.log) …last chars
```

When no extractable headline exists (all lines are blank, markdown-only, or empty after stripping), the placeholder `(no headline available)` is emitted instead so the summary line is never silently dropped for oversized content.

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

When both conditions are met and content exceeds the 4000-cell threshold, Ralph calls the hook with the raw text and emits the result on its own labelled line:

```
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ summary: My first sentence.
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ ai-summary: Higher-level recap produced by the hook.
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] First 400 chars… (+4200 chars, see .agent/raw/dev-1.log) …last chars
```

Hook requirements: the callable must accept `(text: str) -> str | None`. Exceptions are swallowed and treated as None. Output is capped at 400 characters. Ralph provides no built-in hook — integrators supply their own. The hook must be non-blocking.

### Mid-stream checkpoints (default-on)

For very long streaming blocks, Ralph emits a `[content-checkpoint#N]` orientation line every 20 fragments or every 4000 accumulated characters (whichever comes first), with a running fragment count, char total, and deterministic headline:

```
2026-04-20T12:34:56Z INFO CONT [content-checkpoint#20][dev-1] (20 fragments, 4500 chars) My running headline.
```


### Example [run-end] block

```
2026-04-21T12:05:00+00:00 MILESTONE META [run-end] ◆ Ralph run end
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

For terminal phases ( and ), Ralph prints a Rich summary panel
immediately after the  block. The panel title is **Pipeline Complete**
or **Pipeline Failed** and echoes the plan, decision log, metrics,
verification status, commit, PR URL, and open risks seen during the run.
Non-terminal phases (e.g. , ) do not produce a panel.

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
