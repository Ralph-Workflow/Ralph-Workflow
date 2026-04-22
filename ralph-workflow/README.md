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

## Pydoc-first API reference

The public package docstrings are intended to stand on their own. Useful entry points:

```bash
python -m pydoc ralph
python -m pydoc ralph.cli
python -m pydoc ralph.pipeline
python -m pydoc ralph.mcp
python -m pydoc ralph.git
python -m pydoc ralph.workspace
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

**Categories** (`CAT`) group tags into two buckets:

| Category | Meaning |
|----------|---------|
| `META` | Workflow metadata: phase, plan, activity, worker, result, etc. |
| `CONT` | Agent-produced content: text, thinking, tool calls, errors |

**Tags** indicate the source and type of the line:

| Tag | Category | Meaning |
|-----|----------|---------|
| `phase` | META | Workflow phase transition |
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
| `content` | CONT | Agent text output (one-shot, non-streaming) |
| `content-start` | CONT | Start of a streaming text block |
| `content-continue` | CONT | Continuation line in a streaming text block |
| `content-end` | CONT | End of a streaming text block (with headline summary) |
| `thinking` | CONT | Agent thinking/reasoning (one-shot) |
| `thinking-start` | CONT | Start of a streaming thinking block |
| `thinking-continue` | CONT | Continuation of a streaming thinking block |
| `thinking-end` | CONT | End of a streaming thinking block |
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
2026-04-21T12:00:00+00:00 MILESTONE META [phase] ◆ development               # major phase transition
2026-04-21T12:00:01+00:00 INFO META [plan] (no plan loaded yet)              # empty-state placeholder
2026-04-21T12:00:02+00:00 INFO META [activity] agent=claude tool=bash        # metadata about what agent is doing
2026-04-21T12:00:03+00:00 INFO CONT [content-start][dev-1] ↳ summary: Refactored parser to accept streaming deltas  # default-on summary layer
2026-04-21T12:00:03+00:00 INFO CONT [content-start][dev-1] Refactored parser to…  # start of streaming content block
2026-04-21T12:00:04+00:00 INFO CONT [content-continue#2][dev-1] next chunk   # second fragment in the same block
2026-04-21T12:00:05+00:00 INFO CONT [content-end][dev-1] (2 fragments, 850 chars) Refactored parser to accept streaming deltas  # block closed with fragment count, char total, headline
2026-04-21T12:00:06+00:00 INFO CONT [thinking-start][dev-1] I need to check the tests before…  # reasoning/thinking line, distinct tag
2026-04-21T12:00:07+00:00 SUCCESS CONT [tool-result][dev-1] ok               # tool result (SUCCESS level on CONT content)
2026-04-21T12:00:08+00:00 WARN META [progress][dev-1] dropped 3 lines since last flush  # debounced warn when buffer drops
2026-04-21T12:00:09+00:00 INFO CONT [content][dev-1] AAAAA… (+4200 chars, see .agent/raw/dev-1.log) …ZZZZZZZ  # head+tail condensation with overflow reference
```

Tags starting with `content-`, `thinking-`, `tool`, `tool-result`, `error`, or `status-content` are CONT (agent-produced); everything else is META (workflow). Streaming blocks are always closed with a `-end` line before a different unit or a different kind is emitted. A `↳ summary:` line preceding condensed content is an additional, deterministic headline layer — not a replacement for the content itself; the full text is always available at `.agent/raw/<unit>.log`.

## Long-content display

Condensation (head+tail with `(+N chars, see .agent/raw/<unit>.log)`) is the deterministic default for oversized lines and is always active — the summary described below is an additional layer on top.

For content blocks exceeding 4000 display cells, Ralph also emits a `↳ summary:` headline line **before** the condensed excerpt. This summary layer is **default-on** — no environment variable is needed:

```
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] ↳ summary: My first non-empty headline sentence
2026-04-20T12:34:56Z INFO CONT [content-start][dev-1] First 400 chars… (+4200 chars, see .agent/raw/dev-1.log) …last chars
```

To **disable** the summary layer, set `RALPH_LONG_CONTENT_SUMMARY` to one of: `0`, `false`, `no`, `off` (case-insensitive):

```bash
export RALPH_LONG_CONTENT_SUMMARY=0  # disable the summary layer
```

Any other value — including `1`, `true`, `yes`, or an unset variable — leaves the summary enabled.

Inline summaries (emitted above the condensed content) are truncated at 200 characters. Streaming end-line summaries emitted inside `[content-end]`/`[thinking-end]` lines are truncated at 120 characters.

The summary is deterministic: the first non-empty line with markdown heading and quote prefixes stripped, terminated at the first sentence boundary (`.`, `!`, `?`, or newline). No external AI call is made; the upstream provider already produced the text.

The full raw content of any condensed line is preserved to `.agent/raw/<unit-id>.log` so readers always have a path to the complete output.
