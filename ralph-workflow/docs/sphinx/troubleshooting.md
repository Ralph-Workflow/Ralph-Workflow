---
orphan: true
---

# Troubleshooting

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Use this page when a run does not behave the way you expect. It is organized around symptoms, likely causes, and the next command or file to check.

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
- **Codex CLI**: see <https://platform.openai.com/docs/codex>
- **opencode**: see <https://opencode.ai>
- **Nanocoder**: see <https://docs.nanocollective.org/nanocoder/docs>
- **Google Anti Gravity (agy)**: see <https://github.com/google-antigravity/antigravity-cli>

Verify after installation:

```bash
ralph --diagnose
```

The PATH column in the Agents table should show `on PATH` in green.

## AGY transport unavailable on Windows

**Symptom:** The AGY transport fails immediately on Windows, or the run log reports that PTY-backed terminal handling is supported only on POSIX platforms.

**Cause:** Ralph Workflow invokes AGY via a PTY so AGY's `isatty()` check in `--print` mode succeeds. That transport depends on POSIX terminal APIs (`openpty`, `fork`, controlling-terminal setup), so it supports Linux and macOS directly but not native Windows terminals through the same code path.

**Fix:**

- Use WSL2 or a POSIX-compatible environment on Windows.
- Or route the phase to another headless transport such as Codex, OpenCode, or Nanocoder.

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

**Cause:** Ralph Workflow decides idleness from real provider activity, not just from what happened to appear on screen. Streaming deltas, lifecycle events, tool calls, and tool results count as activity; blank heartbeat lines do not. If Ralph Workflow has to kill the subprocess for inactivity, any captured session ID is treated as unsafe and the retry starts fresh unless the transport explicitly supports safe resume after forced termination.

**Fix:** Check the watchdog log line for `reason`, `last_activity_kind`, and `resume_safe`. If the next attempt reports `No conversation found with session ID`, recovery treats it as a stale session and retries fresh within the remaining budget.

## `make verify` fails after editing config

**Symptom:** `ruff`, `mypy`, or `pytest` fails after editing configuration or source files.

**Fix sequence:**

1. Run `make ruff-fix` to auto-fix lint issues.
2. Run `uv run python -m mypy ralph/` to find type errors and fix them manually.
3. Run `uv run pytest tests/ -q` to find failing tests and fix root causes.
4. Re-run `make verify` to confirm all checks pass.

Do not lower coverage thresholds or suppress warnings — fix the underlying issue.

## How to read a `[run-end]` block

The `[run-end]` block is emitted at the end of every pipeline run. Wide mode
(`>= 100` columns) groups counters on a single line:

```
MILESTONE META [run-end] ◆ Ralph Workflow run end
INFO     META [run-end] phase=complete elapsed=42.3s exit=completed
INFO     META [run-end] agent_calls=7 content_blocks=12 thinking_blocks=4 tool_calls=28 errors=0
```

Compact mode (`< 60` columns) uses a condensed 2-line format:

```
MILESTONE META [run-end] complete | 42.3s | completed
INFO     META [run-end] agent=7 content=12 thinking=4 tools=28 errors=0
```

Key fields:

| Field | Meaning |
|-------|---------|
| `phase` | Final phase reached (`complete` = success, `failed` = error) |
| `elapsed` | Total wall-clock time for the run |
| `exit` | Why the run ended: `completed`, `failed`, `interrupted`, or `exited` |
| `content_blocks` | Number of agent text output blocks |
| `tool_calls` | Total MCP tool calls made by all agents |
| `errors` | Number of agent error events |
| `agent_calls` | Total agent subprocess invocations |

## When to use `--no-resume` vs `--resume`

| Flag | When to use |
|------|------------|
| `--resume` | You interrupted a run and want Ralph Workflow to continue from the saved checkpoint |
| `--no-resume` | You want to ignore any saved checkpoint and start fresh |
| (neither) | Default: Ralph Workflow starts a fresh run without loading checkpoint state |

Use `ralph --inspect-checkpoint` to see what the current checkpoint contains before deciding.

## Background child work seems to hang indefinitely

**Symptom:** Ralph Workflow shows *"Background child work still active"* for a long time even
after the agent subprocess has returned. The run never completes.

**Cause:** Ralph Workflow now uses an evidence-backed liveness model instead of assuming that an existing child PID means useful work is still happening. A child is treated as alive only when it renews its progress or heartbeat lease within the configured TTL (default: progress 45 s, heartbeat 15 s). If a process still exists but no fresh evidence remains, Ralph Workflow stops treating it as healthy active work and moves toward retry or recovery.

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

## Default Claude transport exited without completing

**Symptom:** The pipeline retried the default Claude transport, or the run log shows `RESUMABLE_CONTINUE` after a Claude invocation.

**Cause:** Ralph Workflow evaluates completion on the default Claude transport by artifact presence or an explicit `declare_complete` MCP call. If neither signal is present when the subprocess exits, Ralph Workflow classifies the exit as incomplete and retries the session using `--resume SESSION_ID`. This is expected behavior, not a failure.

**Fix:**

- If the agent completed its work but did not write an artifact, confirm the agent wrote its plan or result artifact to `.agent/artifacts/` and called `declare_complete`.
- If the session keeps retrying without completing, check the agent logs for errors and confirm that `.agent/mcp.toml` is configured correctly and that the `declare_complete` tool is accessible.
- To force a fresh start instead of continuing, run `ralph --no-resume`.

See [Recovery](recovery.md) for retry budget and fallover behavior.

## Default Claude transport unavailable on Windows

**Symptom:** The default Claude transport fails immediately on Windows, or the run log reports that PTY-backed terminal handling is supported only on POSIX platforms.

**Cause:** Ralph Workflow may use PTY-backed terminal handling for the default Claude transport on POSIX systems. That transport depends on POSIX terminal APIs (`openpty`, `fork`, controlling-terminal setup), so it supports Linux and macOS directly but not native Windows terminals through the same code path.

**Fix:**

- Use `claude-headless` on Windows when you need Claude specifically.
- Or route the phase to another headless transport such as Codex or OpenCode.
- For a live semantic check of the transport behavior, run `python -m ralph smoke-interactive-claude` on Linux or macOS.

## Related pages

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough
- [Quickstart](quickstart.md) — initial setup and first run
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config file structure and FAQ
- [Recovery](recovery.md) — failure classification and retry behavior
- [MCP Architecture](mcp-architecture.md) — MCP server, tool registry, and dual-alias exposure

## Successful tool result, then wedge

**Symptom:** The agent produces a successful tool result, the live MCP server logs
the result, and then nothing meaningful is emitted before the inactivity timeout
fires. The tool calls log shows `claude tool: <name>` followed by silence.

**Cause:** Before the fix, the MCP server's `tools/list` returned each tool under
its raw name only (e.g. `read_file`), but Claude Code's strict MCP mode invokes
tools by their `mcp__<server>__<tool>` alias (e.g. `mcp__ralph__read_file`). The
strict-MCP call came back as
`<tool_use_error>Error: No such tool available: mcp__<server>__<tool></tool_use_error>`,
the agent emitted nothing meaningful in response, and the watchdog fired
`NO_OUTPUT_DEADLINE`. This looked like a 'successful tool result, then wedge' but
was actually a broken tool registry.

**Fix:** The MCP server now exposes **both** the raw tool name and the
`mcp__<server>__<tool>` alias in `tools/list` for every registered tool. The
`tools/call` handler resolves the alias to the canonical (raw) name before
dispatch, so strict-MCP clients see a tool they can actually invoke. The
recovery classifier routes any 'No such tool available' substring to
`FailureCategory.AGENT` with `reset_tool_registry=True`, so the next attempt
calls `RestartAwareMcpBridge.reset_tool_registry()` to rebuild the visible tool
list — bounded by `_TOOL_REGISTRY_MAX_RESETS` (default 3).

See [MCP Architecture](mcp-architecture.md#mcp-tools) for the dual-alias rule
and [Recovery](recovery.md#tool-availability-failures) for the bounded recovery
path.

## Ctrl+C ignored on a stuck PTY run

**Symptom:** The agent appears wedged (no output for > the idle timeout).
Pressing Ctrl+C once does not interrupt the run. The user has to press Ctrl+C
a second time to force a kill, which terminates the entire pipeline.

**Cause:** Before the fix, the first SIGINT routed through `handle_keyboard_interrupt`
and the `InterruptController.begin_interrupt(grace_period_s=...)` path, which
called the generic `shutdown_all` callback. On a wedged PTY agent run, the
generic shutdown did not target the agent's process group quickly enough, so
the first SIGINT appeared to be ignored. The second SIGINT was caught by the
force-kill handler and escalated to `os._exit` — which is not a clean
shutdown.

**Fix:** `InterruptController` now has a `shutdown_all_for_label` field and a
`kill_label` keyword argument on `begin_interrupt`. The factory
`controller_from_process_manager` wires a closure that calls
`manager.shutdown_all_for_label(label_prefix, grace_period_s=...)`. The runner
passes `kill_label="invoke:"` so the first SIGINT targets the agent's
specific label (e.g. `invoke:claude`). The controller is the single source
of truth for interrupt-driven shutdown — there is no parallel `kill_label`
mechanism in `handle_keyboard_interrupt`.

## Session-resume flag drift

**Symptom:** After a retry, Claude behaves like a fresh session — it
re-reads the prompt, re-explores the workspace, and ignores the prior
session state. The transcript announcement line includes the expected
session id, but the next attempt shows no continuation of prior work.

**Cause:** Before the fix, the interactive Claude command path mixed two
semantically different flags: `--session-id <id>` (create a new session and
tag it) and `--resume <id>` (continue an existing session). A SINGLE
`elif` branch in `_build_claude_interactive_command` emitted
`--session-id` when `initial_session_id` was set, which is a fresh-session
flag. The `agent_invocation` retry path passed a prior session id via
`initial_session_id`, so the retry always started a new session instead of
resuming the old one.

**Fix:** A new helper `ralph.agents.invoke._session_resume.resolve_session_resume_flag`
is the **only** function that knows Claude Code's `--resume` vs `--session-id`
semantics. The `--session-id` `elif` branch is removed; the
`--resume` path goes through the helper. The state field
`last_agent_failure_reason: str` (added to `PipelineState` with an
import-time `model_validator` invariant) drives the resume-or-create
decision.

## Tool-availability failures

**Symptom:** The recovery controller reports `reset_tool_registry=True`
on a failure that recurs multiple times. The error message in the recovery
log contains the substring `tool-registry-reset exhausted` and the
run terminates with a hard cap error.

**Cause:** The recovery classifier routes failures containing the substring
`"no such tool available"` (case-insensitive) to
`FailureCategory.AGENT` with `reset_tool_registry=True`. Each subsequent
attempt calls `RestartAwareMcpBridge.reset_tool_registry()`, which
increments the bridge's `tool_registry_resets` counter. The counter is
capped at `_TOOL_REGISTRY_MAX_RESETS` (default 3). After the cap, the
bridge raises `McpServerError` with a message containing
`'tool-registry-reset exhausted'` and the current count.

**Three additive caps** (each is independent and the orchestrator can
distinguish which one fired by the error message substring):

1. `tool-registry-reset exhausted` — the new
   `_TOOL_REGISTRY_MAX_RESETS` cap, raised by
   `RestartAwareMcpBridge.reset_tool_registry()` after 3 resets.
2. `restart budget` + `exhausted` — the existing
   `McpRestartPolicy.max_restarts` cap, raised by
   `RestartAwareMcpBridge.check_health_and_restart_if_needed()` after
   the configured number of crash restarts.
3. `recovery-attempt exhausted` — the existing
   `max_recovery_attempts` cap, raised by the recovery controller
   after the configured number of agent-invocation retries.

**Fix:** If `tool-registry-reset exhausted` fires, the bridge cannot
rebuild the visible tool list. Check the agent logs for repeated
`No such tool available` errors. The most common cause is a
mismatched alias in the live MCP server's `tools/list` response — see
[MCP Architecture](mcp-architecture.md#mcp-tools) for the dual-alias
rule. The counter starts at zero, so a fresh bridge gives 3 resets
of headroom.
