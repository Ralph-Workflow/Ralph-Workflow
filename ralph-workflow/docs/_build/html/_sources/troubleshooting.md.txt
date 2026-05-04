# Troubleshooting

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Frequently asked questions and common issues when using Ralph Workflow.

## I just installed Ralph Workflow and don't know what to do

See the [Getting Started](getting-started.md) walkthrough — it takes you from install
to your first pipeline run step by step, without assuming any prior knowledge.

## PROMPT.md still has the starter sentinel

**Symptom:** Running `ralph` fails immediately with an error about the starter template.

**Cause:** The `PROMPT.md` file still contains the `<!-- ralph:starter-prompt ... -->` sentinel
that `ralph --init` places at the top. Ralph Workflow refuses to run while this sentinel is
present so you cannot accidentally run the pipeline against the placeholder task.

**Fix:** Open `PROMPT.md`, replace the example content with your actual task description,
and remove the sentinel comment at the top. Then re-run `ralph`.

See [Concepts](concepts.md) for what a good PROMPT.md should contain.

## No agents on PATH

**Symptom:** `ralph --diagnose` shows agents as `missing` in the PATH column, or the pipeline
fails when it tries to invoke an agent.

**Fix:** Install the agent binary and ensure it is on your `PATH`:

- **Claude Code**: see <https://docs.claude.com/claude-code>
- **opencode**: see <https://opencode.ai>

Verify after installation:

```bash
ralph --diagnose
```

The PATH column in the Agents table should show `on PATH` in green.

## MCP servers fail to start

**Symptom:** `ralph --check-mcp` or `ralph --diagnose` reports MCP server errors.

**Common causes and fixes:**

1. **Wrong command path** — check the `command` field in `.agent/mcp.toml`. Ensure the
   binary exists and is executable.
2. **Missing environment variables** — some MCP servers require API keys or tokens. Add
   them to your shell environment or to the `env` section in `.agent/mcp.toml`.
3. **Port conflict** — if your MCP server uses a fixed port, check that no other process
   is using it.

Validate after fixing:

```bash
ralph --check-mcp
```

## Agent run times out even though the transcript showed activity

**Symptom:** Ralph Workflow reports an inactivity timeout or a stale session retry after an
agent run that appeared active.

**Cause:** Idle timeout follows provider activity signals before display rendering. Real
streaming deltas, lifecycle events, tool calls, and tool results count as activity; blank
heartbeat lines do not. If Ralph Workflow has to kill the subprocess for inactivity, any captured
session ID is considered unsafe and the retry starts fresh unless the transport explicitly
supports safe resume after forced termination.

**Fix:** Check the watchdog log line for `reason`, `last_activity_kind`, and `resume_safe`.
If the next attempt reports `No conversation found with session ID`, recovery treats it as a
stale session and retries fresh within the remaining budget.

## `make verify` fails after editing config

**Symptom:** `ruff`, `mypy`, or `pytest` fails after editing configuration or source files.

**Fix sequence:**

1. Run `make ruff-fix` to auto-fix lint issues.
2. Run `uv run python -m mypy ralph/` to find type errors and fix them manually.
3. Run `uv run pytest tests/ -q` to find failing tests and fix root causes.
4. Re-run `make verify` to confirm all checks pass.

Do not lower coverage thresholds or suppress warnings — fix the underlying issue.

## How to read a `[run-end]` block

The `[run-end]` block is emitted at the end of every pipeline run:

```
MILESTONE META [run-end] ◆ Ralph Workflow run end
INFO META [run-end] phase=complete
INFO META [run-end] elapsed=42.3s
INFO META [run-end] content_blocks=12
INFO META [run-end] thinking_blocks=4
INFO META [run-end] tool_calls=28
INFO META [run-end] errors=0
INFO META [run-end] agent_calls=7
```

Key fields:

| Field | Meaning |
|-------|---------|
| `phase` | Final phase reached (`complete` = success, `failed` = error) |
| `elapsed` | Total wall-clock time for the run |
| `content_blocks` | Number of agent text output blocks |
| `tool_calls` | Total MCP tool calls made by all agents |
| `errors` | Number of agent error events |
| `agent_calls` | Total agent subprocess invocations |

## When to use `--no-resume` vs `--resume`

| Flag | When to use |
|------|------------|
| `--resume` | You interrupted a run and want to continue from the last completed phase |
| `--no-resume` | The checkpoint is stale or from a different task; start fresh |
| (neither) | Default: Ralph Workflow uses a checkpoint if one exists, otherwise starts fresh |

Use `ralph --inspect-checkpoint` to see what the current checkpoint contains before deciding.

## Background child work seems to hang indefinitely

**Symptom:** Ralph Workflow shows *"Background child work still active"* for a long time even
after the agent subprocess has returned. The run never completes.

**Cause (before v1.1):** Process existence alone was used to decide whether a child was
still alive. A stale label or orphaned PID could keep `WAITING_ON_CHILD` open forever.

**Current behaviour:** Ralph Workflow uses an evidence-backed liveness model. A child is only
considered alive when it has renewed its progress or heartbeat lease within the configured
TTL (default: progress 45 s, heartbeat 15 s). A stale process without fresh evidence is
not treated as active; the parent falls back to OS-descendant detection, and if that also
clears, escalates to a resumable-retry path.

The waiting status log line includes `alive_by=` to explain the active evidence:

```
Background child work still active (run=120s, cumulative=240s, ceiling=600s, alive_by=fresh_heartbeat_only)
```

If you see `alive_by=stale_label_only` or `alive_by=os_descendant_only_stale_progress`,
the child has gone quiet and the watchdog will apply the shorter **no-progress ceiling**
(default: 600 s) instead of the full ceiling (1800 s). This means a stuck child
that is not making progress will be detected and escalated faster.

The effective ceiling used is also visible in the HARD_STOP diagnostic as `effective_ceiling`:
- `effective_ceiling=no_progress` — shorter no-progress ceiling fired (child was not making progress)
- `effective_ceiling=standard` — full ceiling fired (child was making progress until the end)

**Fix if child genuinely hangs:** Check the child agent log for errors. The parent
will fire `CHILDREN_PERSIST_TOO_LONG` when the applicable ceiling is reached:
- No-progress ceiling (default 600 s) if child is alive but not making progress
- Full ceiling (default 1800 s) if child is making genuine progress

**Tuning the no-progress ceiling:** To disable the no-progress ceiling entirely and always
use the full 1800 s ceiling, set `agent_idle_no_progress_waiting_on_child_seconds = null` in
your TOML config. This is not recommended unless you have workloads with legitimately long
quiet periods between progress signals.

## Related pages

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough
- [Quickstart](quickstart.md) — initial setup and first run
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config file structure and FAQ
- [Recovery](recovery.md) — failure classification and retry behavior
