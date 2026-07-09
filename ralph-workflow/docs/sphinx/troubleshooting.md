# Troubleshooting

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Use this page when a run does not behave the way you expect. It is organized around symptoms, likely causes, and the next command or file to check.

## I just installed Ralph Workflow and don't know what to do

See the [Getting Started](getting-started.md) walkthrough — it takes you from install to your first pipeline run step by step, without assuming any prior knowledge.

## PROMPT.md still has the starter sentinel

**Symptom:** Running `ralph` fails immediately with an error about the starter template.

**Cause:** The `PROMPT.md` file still contains the `<!-- ralph:starter-prompt ... -->` sentinel that `ralph --init` places at the top. Ralph Workflow refuses to run while this sentinel is present so you cannot accidentally run the pipeline against the placeholder task.

**Fix:** Open `PROMPT.md`, replace the example content with your actual task description, and remove the sentinel comment at the top. Then the human operator can re-run `ralph` from their shell.

## No agents on PATH

**Symptom:** `ralph --diagnose` shows agents as `missing` in the PATH column, or the pipeline fails when it tries to invoke an agent.

**Fix:** Install the agent binary and ensure it is on your `PATH`:

- **Claude Code**: see <https://docs.claude.com/claude-code>
- **Codex CLI**: see <https://platform.openai.com/docs/codex>
- **opencode**: see <https://opencode.ai>
- **Nanocoder**: see <https://docs.nanocollective.org/nanocoder/docs>
- **Google Anti Gravity (agy)**: see <https://github.com/google-antigravity/antigravity-cli>
- **Pi.dev (pi)**: see <https://pi.dev/docs/latest/usage>
- **Cursor Agent (agent)**: see <https://docs.cursor.com/agent>

Verify after installation:

```bash
ralph --diagnose
```

The PATH column in the Agents table should show `on PATH` in green.

## Cursor agent not detected

**Symptom:** `ralph --diagnose` reports the Cursor agent (`agent` binary) is
`missing` even though `which agent` returns a path, or `ralph
smoke-interactive-cursor` fails with a `cursor binary not found` diagnostic.

**Fix:** Verify the `agent` binary is on `PATH` from the same shell you
launch `ralph` from:

```bash
agent --version
```

The headless smoke path requires `agent login` (or the
`CURSOR_API_KEY` env var) for the live binary.  Set `RALPH_CURSOR_BINARY`
to a custom wrapper, alternate live binary, or operator-wired test
stub if the binary is not on `PATH`; non-executable paths are
ignored with a WARNING.

## AGY transport unavailable on Windows

**Symptom:** The AGY transport fails immediately on Windows, or the run log reports that PTY-backed terminal handling is supported only on POSIX platforms.

**Cause:** Ralph Workflow invokes AGY via a PTY so AGY's `isatty()` check in `--print` mode succeeds. That transport depends on POSIX terminal APIs (`openpty`, `fork`, controlling-terminal setup), so it supports Linux and macOS directly but not native Windows terminals through the same code path.

**Fix:**

- Use WSL2 or a POSIX-compatible environment on Windows.
- Or route the phase to another headless transport such as Codex, OpenCode, or Nanocoder.

The full AGY and Pi end-to-end smoke walkthroughs (including mock-backed deterministic verification, parity table column meanings, mock-vs-live diagnostic paths, and how the upstream `agy` binary or the local `pi` binary is exercised) live in [ralph-workflow/docs/agents/architecture.md](agents/architecture.md#agy-and-pi-end-to-end-smoke-walkthroughs). That page is the canonical home for the per-transport smoke details; the troubleshooting index stays short on purpose.

## MCP servers fail to start

**Symptom:** `ralph --check-mcp` or `ralph --diagnose` reports MCP server errors.

**Common causes and fixes:**

1. **Wrong command path** — check the `command` field in `.agent/mcp.toml`. Ensure the binary exists and is executable.
2. **Missing environment variables** — some MCP servers require API keys or tokens. Add them to your shell environment or to the `env` section in `.agent/mcp.toml`.
3. **Port conflict** — if your MCP server uses a fixed port, check that no other process is using it.

Validate after fixing:

```bash
ralph --check-mcp
```

## Agent run times out even though the transcript showed activity

**Symptom:** Ralph Workflow reports an inactivity timeout or a stale session retry after an agent run that appeared active.

**Cause:** Ralph Workflow decides idleness from real provider activity, not just from what happened to appear on screen. Streaming deltas, lifecycle events, tool calls, and tool results count as activity; blank heartbeat lines do not. If Ralph Workflow has to kill the subprocess for inactivity, any captured session ID is treated as unsafe and the retry starts fresh unless the transport explicitly supports safe resume after forced termination.

**Fix:** Check the watchdog log line for `reason`, `last_activity_kind`, and `resume_safe`. If the next attempt reports `No conversation found with session ID`, recovery treats it as a stale session and retries fresh within the remaining budget.

## Interactive agent reports an invalid provider or model

**Symptom:** An interactive agent starts, prints a provider/model configuration error, and makes no task progress. For Nanocoder this can look like `Provider '...' not found in agents.config.json`.

**Cause:** Interactive agents can validate provider/model configuration inside their TUI after the PTY has already started. Ralph Workflow treats these startup/configuration lines as terminal invocation failures, not as normal model output.

**Fix:** Run the smoke test with the exact alias from the pipeline chain, for example:

```bash
python -m ralph smoke-interactive-nanocoder --agent 'nanocoder/MiniMax Coding/MiniMax-M3'
```

If the smoke report shows the same provider/model error, fix the agent's provider/model alias or local agent config before starting an unattended run.

## Nanocoder shows the welcome screen but does not start the task

**Symptom:** Nanocoder prints its banner and `Tips for getting started`, then shows the task text as pasted input or sits at `What would you like me to help with?` without tool or model progress.

**Cause:** Nanocoder has two bad integration traps. Its JSON/plain automation path has a hidden long-run action limit, observed around 100 actions, so Ralph Workflow must keep Nanocoder on the PTY-backed Ink runtime. At the same time, Nanocoder's interactive editor buffer is not a stable prompt-submission API. If Ralph Workflow drives the editor by pasted PTY input, startup output can leave the task text in the editor instead of submitting a model turn.

**Fix:** Run the Nanocoder smoke test with the same alias used by the pipeline:

```bash
python -m ralph smoke-interactive-nanocoder --agent 'nanocoder/MiniMax Coding/MiniMax-M3'
```

The smoke test should fail if prompt submission regresses. If an older run is already stuck at the Nanocoder welcome screen, stop that run and restart it after upgrading Ralph Workflow.

## Nanocoder exits with "Conversation exceeded 50 turns"

**Symptom:** A Nanocoder run fails partway through a complex task with the message `Conversation exceeded 50 turns` in the run log.

**Cause:** Nanocoder's JSON/plain automation path has a hidden long-run action limit. The visible failure may appear as `Conversation exceeded 50 turns`, and longer JSON/plain runs have also been observed to bug out around 100 actions. Ralph Workflow's maintained Nanocoder path must stay on the PTY-backed Ink runtime instead of relying on JSON/plain mode as the durable backend.

**Fix options:**

- **Use a different agent for complex tasks.** Claude Code, OpenCode, and Google Anti Gravity do not have an equivalent per-run turn cap when invoked headlessly. If your task regularly exceeds 50 tool exchanges, route that phase to one of those agents.
- **Keep Nanocoder on the maintained interactive path.** Do not switch Ralph Workflow's Nanocoder backend to JSON/plain mode to avoid TUI complexity; that reintroduces the hidden action-limit bug.
- **Break the task into smaller phases.** If Nanocoder is your only option, split the `PROMPT.md` task into smaller steps. Ralph Workflow's checkpoint and phase system is designed to compose smaller phase outputs.

## `make verify` fails after editing config

**Symptom:** `ruff`, `mypy`, or `pytest` fails after editing configuration or source files.

**Fix sequence:**

1. Run `make ruff-fix` to auto-fix lint issues.
2. Run `uv run python -m mypy ralph/` to find type errors and fix them manually.
3. Run `uv run pytest tests/ -q` to find failing tests and fix root causes.
4. Re-run `make verify` to confirm all checks pass.

Do not lower coverage thresholds or suppress warnings — fix the underlying issue.

## How to read a `[run-end]` block

The `[run-end]` block is emitted at the end of every pipeline run. Ralph Workflow exposes exactly ONE display mode: ``default``. There is no width-based dispatch. The block always uses the same multi-line shape at every terminal width, with counters grouped on the second and third lines:

```
MILESTONE META [run-end] ◆ Ralph Workflow run end
INFO     META [run-end] phase=complete elapsed=42.3s exit=completed
INFO     META [run-end] agent_calls=7 content_blocks=12 thinking_blocks=4 tool_calls=28 errors=0
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

These are operator-side shell flags for the human running Ralph Workflow. They are not instructions for an in-session agent to spawn another `ralph` process recursively.

| Flag | When to use |
|------|------------|
| `--resume` | You interrupted a run and want Ralph Workflow to continue from the saved checkpoint |
| `--no-resume` | You want to ignore any saved checkpoint and start fresh |
| (neither) | Default: Ralph Workflow starts a fresh run without loading checkpoint state |

From the human operator shell, `ralph --inspect-checkpoint` shows what the current checkpoint contains before deciding.

## Background child work seems to hang indefinitely

**Symptom:** Ralph Workflow shows *"Background child work still active"* for a long time even after the agent subprocess has returned. The run never completes.

**Cause:** Ralph Workflow now uses an evidence-backed liveness model instead of assuming that an existing child PID means useful work is still happening. A child is treated as alive only when it renews its progress or heartbeat lease within the configured TTL. If a process still exists but no fresh evidence remains, Ralph Workflow stops treating it as healthy active work and moves toward retry or recovery.

The waiting status log line includes `alive_by=` to explain the active evidence:

```
Background child work still active (run=120s, cumulative=240s, ceiling=600s, alive_by=fresh_heartbeat_only)
```

If you see `alive_by=stale_label_only` or `alive_by=os_descendant_only_stale_progress`, the child has gone quiet and the watchdog will apply the shorter **no-progress ceiling** (default: 600 s) instead of the full ceiling (1800 s). This means a stuck child that is not making progress will be detected and escalated faster.

**Fix if child genuinely hangs:** Check the child agent log for errors. The parent will fire `CHILDREN_PERSIST_TOO_LONG` when the applicable ceiling is reached:
- No-progress ceiling (default 600 s) if child is alive but not making progress
- Full ceiling (default 1800 s) if child is making genuine progress

## Default Claude transport exited without completing

**Symptom:** The pipeline retried the default Claude transport, or the run log shows `RESUMABLE_CONTINUE` after a Claude invocation.

The detailed completion-evidence walkthrough (canonical SQLite store, fallback paths, repair loop, single-shot and staged-flow checks) lives in [Recovery](recovery.md).

## Default Claude transport unavailable on Windows

**Symptom:** The default Claude transport fails immediately on Windows, or the run log reports that PTY-backed terminal handling is supported only on POSIX platforms.

**Cause:** Ralph Workflow may use PTY-backed terminal handling for the default Claude transport on POSIX systems. That transport depends on POSIX terminal APIs (`openpty`, `fork`, controlling-terminal setup), so it supports Linux and macOS directly but not native Windows terminals through the same code path.

**Fix:**

- Use `claude-headless` on Windows when you need Claude specifically.
- Or route the phase to another headless transport such as Codex or OpenCode.
- For a live semantic check of the transport behavior, the human operator can run `python -m ralph smoke-interactive-claude` on Linux or macOS from a shell outside the managed agent session.

## Ctrl+C ignored on a stuck PTY run

**Symptom:** The agent appears wedged (no output for > the idle timeout). Pressing Ctrl+C once does not interrupt the run. The user has to press Ctrl+C a second time to force a kill, which terminates the entire pipeline.

**Cause:** Before the fix, the first SIGINT routed through `handle_keyboard_interrupt` and the `InterruptController.begin_interrupt(grace_period_s=...)` path, which called the generic `shutdown_all` callback. On a wedged PTY agent run, the generic shutdown did not target the agent's process group quickly enough, so the first SIGINT appeared to be ignored.

**Fix:** `InterruptController` now has a `shutdown_all_for_label` field and a `kill_label` keyword argument on `begin_interrupt`. The factory `controller_from_process_manager` wires a closure that calls `manager.shutdown_all_for_label(label_prefix, grace_period_s=...)`. The runner passes `kill_label="invoke:"` so the first SIGINT targets the agent's specific label (e.g. `invoke:claude`). The controller is the single source of truth for interrupt-driven shutdown — there is no parallel `kill_label` mechanism in `handle_keyboard_interrupt`.

## Successful tool result, then wedge

**Symptom:** The agent produces a successful tool result, the live MCP server logs the result, and then nothing meaningful is emitted before the inactivity timeout fires.

See [MCP Architecture](mcp-architecture.md#mcp-tools) for the dual-alias rule and [Recovery](recovery.md#tool-availability-failures) for the bounded recovery path.

## Tool-availability failures

**Symptom:** The recovery controller reports `reset_tool_registry=True` on a failure that recurs multiple times. The error message in the recovery log contains the substring `tool-registry-reset exhausted` and the run terminates with a hard cap error.

See [Recovery](recovery.md#tool-availability-failures) for the three additive caps and the bounded recovery path.

## Related pages

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config file structure and FAQ
- [Recovery](recovery.md) — failure classification and retry behavior
- [MCP Architecture](mcp-architecture.md) — MCP server, tool registry, and dual-alias exposure
