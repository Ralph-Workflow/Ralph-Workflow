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
and remove the sentinel comment at the top. Then the human operator can re-run `ralph`
from their shell.

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
- **Pi.dev (pi)**: see <https://pi.dev/docs/latest/usage>

Verify after installation:

```bash
ralph --diagnose
```

Run this from the human operator shell outside any Ralph-managed agent session.

The PATH column in the Agents table should show `on PATH` in green.

## AGY transport unavailable on Windows

**Symptom:** The AGY transport fails immediately on Windows, or the run log reports that PTY-backed terminal handling is supported only on POSIX platforms.

**Cause:** Ralph Workflow invokes AGY via a PTY so AGY's `isatty()` check in `--print` mode succeeds. That transport depends on POSIX terminal APIs (`openpty`, `fork`, controlling-terminal setup), so it supports Linux and macOS directly but not native Windows terminals through the same code path.

**Fix:**

- Use WSL2 or a POSIX-compatible environment on Windows.
- Or route the phase to another headless transport such as Codex, OpenCode, or Nanocoder.

## AGY transport end-to-end smoke

**Symptom:** You want to verify that the AGY transport is wired correctly from Ralph Workflow through the live `agy` binary.

**Fix:**

- Run the canonical AGY smoke test on Linux or macOS:

```bash
python -m ralph smoke-interactive-agy
```

Run this from the human operator shell outside any Ralph-managed agent session.

The parity table reports five acceptance signals:

| Column | Green means |
|--------|-------------|
| File | `tmp/interactive-agy-smoke/todo-list.js` was created |
| Session | A session ID was observed in the transcript |
| Parser events | The transcript produced parseable events (Claude parity only) |
| Tool activity | Tool-use/tool-result signals or the artifact's `headless_guide_checks` were observed |
| Artifact | The `smoke_test_result` artifact was submitted |

A red column in File, Tool activity, or Artifact indicates a Ralph Workflow regression. The Session and Parser events columns may show `missing`/`0` on AGY headless `--print` runs: AGY does not emit a session ID or parser-friendly stdout stream in `--print` mode (verified in `tmp/agy-live-transcript.txt`). Because AGY's headless `--print` mode does not reliably call Ralph Workflow's streamable-HTTP MCP tools, the smoke prompt instructs AGY to write the `smoke_test_result` artifact directly to `.agent/artifacts/smoke_test_result.json`; tool activity is then inferred from that artifact. The rationale for removing the non-functional `session_flag` from the builtin AGY config is recorded in `ralph-workflow/CHANGELOG.md` under the 'Google Anti Gravity (AGY) is now a first-class supported agent path' entry.

If AGY exits 0 but the parity table reports no file, no artifact, and the `Breaks` column contains `AGY --print returned empty stdout: ...`, the upstream `agy` binary itself produced no stdout. The smoke detector reads `~/.gemini/antigravity-cli/cli.log` and reports the measured root cause in the `Breaks` column. The most common upstream conditions are an individual API quota exhausted error (`429 RESOURCE_EXHAUSTED`), whose diagnostic names the reset window, or an unrecognized model ID. Lowercased or slashed slugs such as `agy/gemini-3.5-flash-low` are not accepted by AGY v1.0.8; use the exact display names from `agy models`. The eight canonical names are `Gemini 3.5 Flash (Medium)`, `Gemini 3.5 Flash (High)`, `Gemini 3.5 Flash (Low)`, `Gemini 3.1 Pro (Low)`, `Gemini 3.1 Pro (High)`, `Claude Sonnet 4.6 (Thinking)`, `Claude Opus 4.6 (Thinking)`, and `GPT-OSS 120B (Medium)`. See `tmp/agy-source-of-truth.txt` for the current measured wire format. These are upstream AGY conditions, not Ralph Workflow regressions; wait for the quota reset or use a recognized model alias. Use `--agent agy/<model>` to pin a different model alias.

### Distinguishing live-quota failure from mock-quota output

#### Live binary re-measured (2026-06-15)

The live `agy` v1.0.8 binary was re-measured on 2026-06-15T13:32:29Z. The upstream source URLs were re-fetched and confirmed (CHANGELOG, README, release tag 1.0.8, issue #76, cli-using docs, cli-reference docs). The local binary was probed with the canonical Ralph Workflow flag order `agy --dangerously-skip-permissions --model 'Claude Sonnet 4.6 (Thinking)' --print 'Reply with exactly the word: hello'` and returned stdout `hello` (exit 0). The `~/.gemini/antigravity-cli/cli.log` shows no `RESOURCE_EXHAUSTED (429)` condition — quota has fully reset. A live `python -m ralph smoke-interactive-agy` run captured to `tmp/smoke-interactive-agy-run.log` reports file=yes, tool activity=yes, artifact=yes, breaks=none. See `tmp/agy-source-of-truth.txt` sections `=== UPSTREAM SOURCE RE-VALIDATION (2026-06-15T13:32:29Z) ===` and `=== LOCAL RE-MEASUREMENT (2026-06-15T13:32:29Z) ===`.

When running with `RALPH_AGY_BINARY` set (for example to the deterministic mock at `tests/_support/mock_agy.sh` for CI), an empty stdout with `MOCK_AGY_BEHAVIOR=quota_exhausted` is expected and reported as an informational break, not as the live upstream quota diagnostic. The mock entrypoint is `tests/_support/mock_agy.py` (run as `python -m tests._support.mock_agy`); `mock_agy.sh` is a thin wrapper suitable for `RALPH_AGY_BINARY`. To verify the harness itself, run the mock without that variable:

```bash
RALPH_AGY_BINARY=tests/_support/mock_agy.sh python -m ralph smoke-interactive-agy
```

Run this from the human operator shell outside any Ralph-managed agent session.

This should report file=yes, artifact=yes, and no upstream-quota break.

## Pi.dev transport end-to-end smoke

**Symptom:** You want to verify that the Pi (pi.dev) transport is wired correctly, that the documented `AgentSessionEvent` NDJSON format parses without error, and that `pi --mode json <prompt>` produces the expected argv.

**Fix:** Two pytest suites cover the public surface end-to-end without touching the network or a real `pi` binary:

```bash
# Drive the public surface (AgentRegistry -> catalog.get('pi') -> build_command)
uv run pytest tests/agents/test_pi_dev_blackbox.py -q

# Pin the documented AgentSessionEvent vocabulary against the committed fixture
uv run pytest tests/agents/parsers/test_pi_dev_wire_format_spec.py -q
```

Both tests are pure-Python (no `time.sleep`, no real subprocess, no network), so they pass deterministically under the 60 s combined test budget enforced by `make verify`. The wire-format spec test loads the committed fixture at `tests/agents/parsers/fixtures/pi_dev_documented_events.json` (NOT the transient `tmp/pi-dev-docs/inventory.md`), so a clean-checkout run does not depend on transient state.

The argv assertion in the black-box test ends with the actual prompt TEXT loaded from a `tmp_path` fixture (e.g. `hello world`) per the public contract in `ralph-workflow/ralph/agents/invoke/_command_builders/__init__.py:_load_prompt_text` with `positional_prompt=True`. Do NOT assert the literal `'PROMPT.md'` - that is the prompt file PATH, not the file CONTENT that the positional argv element carries.

For the live `pi` binary end-to-end path, see <https://pi.dev/docs/latest/usage> for the documented `--mode json` invocation and the documented `--approve` (`-a`) project-trust override. Note that pi.dev has no documented CLI MCP wiring path (the "Pi keeps the core small" design philosophy explicitly omits built-in MCP), so Ralph Workflow removes `RALPH_MCP_ENDPOINT` from the Pi subprocess environment and relies on the prompt-side artifact fallback instead of MCP tool calls.

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

Run this from the human operator shell outside any Ralph-managed agent session.

## Agent run times out even though the transcript showed activity

**Symptom:** Ralph Workflow reports an inactivity timeout or a stale session retry after an
agent run that appeared active.

**Cause:** Ralph Workflow decides idleness from real provider activity, not just from what happened to appear on screen. Streaming deltas, lifecycle events, tool calls, and tool results count as activity; blank heartbeat lines do not. If Ralph Workflow has to kill the subprocess for inactivity, any captured session ID is treated as unsafe and the retry starts fresh unless the transport explicitly supports safe resume after forced termination.

**Fix:** Check the watchdog log line for `reason`, `last_activity_kind`, and `resume_safe`. If the next attempt reports `No conversation found with session ID`, recovery treats it as a stale session and retries fresh within the remaining budget.

## Nanocoder exits with "Conversation exceeded 50 turns"

**Symptom:** A Nanocoder run fails partway through a complex task with the message `Conversation exceeded 50 turns` in the run log.

**Cause:** Ralph Workflow invokes Nanocoder using its headless `plain` runtime (the lightweight, Ink-free path that auto-enables in non-TTY subprocess environments). That runtime contains a hardcoded `MAX_TURNS = 50` cap in `plain/conversation.js`. There is no CLI flag, environment variable, or config option to raise this limit. The Ink (TUI) runtime has no such cap, but it requires a real TTY and cannot be used via subprocess pipe.

**Fix options:**

- **Use a different agent for complex tasks.** Claude Code, OpenCode, and Google Anti Gravity do not have an equivalent per-run turn cap when invoked headlessly. If your task regularly exceeds 50 tool exchanges, route that phase to one of those agents.
- **Break the task into smaller phases.** If Nanocoder is your only option, split the `PROMPT.md` task into steps that each complete within 50 turns. Ralph Workflow's checkpoint and phase system is designed to compose smaller phase outputs.
- **Track the upstream issue.** The 50-turn cap is undocumented and a known Nanocoder limitation. If the Nanocoder project raises or removes this cap in a future release, Ralph Workflow will pick it up automatically — no code change needed on Ralph Workflow's side.

## `make verify` fails after editing config

**Symptom:** `ruff`, `mypy`, or `pytest` fails after editing configuration or source files.

**Fix sequence:**

1. Run `make ruff-fix` to auto-fix lint issues.
2. Run `uv run python -m mypy ralph/` to find type errors and fix them manually.
3. Run `uv run pytest tests/ -q` to find failing tests and fix root causes.
4. Re-run `make verify` to confirm all checks pass.

Do not lower coverage thresholds or suppress warnings — fix the underlying issue.

## How to read a `[run-end]` block

The `[run-end]` block is emitted at the end of every pipeline run.
Ralph Workflow exposes exactly ONE display mode: ``default``. There is no
width-based dispatch. The block always uses the same multi-line shape at
every terminal width, with counters grouped on the second and third lines:

```
MILESTONE META [run-end] ◆ Ralph Workflow run end
INFO     META [run-end] phase=complete elapsed=42.3s exit=completed
INFO     META [run-end] agent_calls=7 content_blocks=12 thinking_blocks=4 tool_calls=28 errors=0
```

.. note::

   What changed and why it belongs here

   The historical three-tier mode split (narrow / medium / wide) is gone.
   The ``[run-end]`` block now renders the same multi-line shape at every
   terminal width; the persistent bottom Status Bar renders all applicable
   fields (working directory, active phase, applicable outer development
   iteration, applicable inner analysis iteration) at every terminal width
   where they fit. At widths >= 40 cols the canonical ``Dev N/cap`` /
   ``Analysis N/cap`` labels render in full and only path
   middle-truncation and phase tail-truncation budgets adapt to width.
   Below 40 cols the implementation may degrade to compact
   (``D1/3`` / ``A2/5``) or minimal (``1/3`` / ``2/5``) forms to fit.
   Below 14 cols the iteration segments drop one at a time (outer_dev
   first, then inner_analysis, then both) so the bar never overflows the
   working area; phase and path remain visible at every applicable
   width. This belongs on the troubleshooting reference page because
   operators who previously diagnosed narrow-terminal output by switching
   modes no longer have that lever; the consolidated single mode means
   there is exactly one shape to recognise. What was pruned: the
   wide-mode single-line counter grouping and the compact-mode 2-line
   condensed format. What was merged: every width-driven branch in
   ``parallel_display.py`` now renders identically.

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

These are operator-side shell flags for the human running Ralph Workflow. They are not
instructions for an in-session agent to spawn another `ralph` process recursively.

| Flag | When to use |
|------|------------|
| `--resume` | You interrupted a run and want Ralph Workflow to continue from the saved checkpoint |
| `--no-resume` | You want to ignore any saved checkpoint and start fresh |
| (neither) | Default: Ralph Workflow starts a fresh run without loading checkpoint state |

From the human operator shell, `ralph --inspect-checkpoint` shows what the current checkpoint contains before deciding.

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

The commands in this section are operator-side shell commands for the human running Ralph Workflow. They are not instructions for an agent inside a Ralph-managed session to spawn another `ralph` process recursively.

**Cause:** Ralph Workflow evaluates completion on the default Claude transport from durable completion evidence. A run-scoped artifact receipt is sufficient completion evidence for required-artifact flows, and single-shot artifact submissions also write the completion sentinel automatically after a successful submit. If the required completion evidence is missing when the subprocess exits, Ralph Workflow classifies the exit as incomplete and resumes the underlying agent session internally. This is expected behavior, not a failure.

**Fix:**

- If the agent completed a single-shot artifact submission, confirm the canonical artifact was written and that the submit path completed successfully.
- Check the concrete completion evidence files for the current run: `.agent/receipts/<run_id>/<artifact_type>.json` and `.agent/completion_seen_<run_id>.json`.
- If the agent used the fallback file path instead of a successful MCP submit, inspect `.agent/tmp/<artifact_type>.json` first, then `.agent/artifacts/<artifact_type>.json` for direct-write paths such as the AGY fallback. The fallback payload must be promoted into the canonical chain before completion is considered satisfied.
- If the agent was in a multi-step flow such as staged plan drafting, confirm it reached the artifact-writing completion step for that flow (for example `ralph_finalize_plan`) and that the run-scoped receipt was written.
- If the session keeps retrying without completing, check the agent logs for errors and confirm that `.agent/mcp.toml` is configured correctly and that the required completion tool for the active flow is accessible.
- If the MCP server rejected an artifact payload, follow the repair loop from the referenced doc: read `.agent/artifact-formats/<type>.md` or `.agent/artifact-formats/artifact_formats_index.md`, rebuild the payload or artifact_type, and retry the same MCP tool. For plan rejections, repair the staged draft with the plan staging tools, then rerun `ralph_validate_draft` or `ralph_finalize_plan`.
- To force a fresh start instead of continuing, the human operator can choose the `--no-resume` startup path from their shell outside the agent session.

See [Recovery](recovery.md) for retry budget and fallover behavior.

## Default Claude transport unavailable on Windows

**Symptom:** The default Claude transport fails immediately on Windows, or the run log reports that PTY-backed terminal handling is supported only on POSIX platforms.

**Cause:** Ralph Workflow may use PTY-backed terminal handling for the default Claude transport on POSIX systems. That transport depends on POSIX terminal APIs (`openpty`, `fork`, controlling-terminal setup), so it supports Linux and macOS directly but not native Windows terminals through the same code path.

**Fix:**

- Use `claude-headless` on Windows when you need Claude specifically.
- Or route the phase to another headless transport such as Codex or OpenCode.
- For a live semantic check of the transport behavior, the human operator can run `python -m ralph smoke-interactive-claude` on Linux or macOS from a shell outside the managed agent session.

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

The two CLI catches (in `ralph.cli.main._run_pipeline` and
`ralph.cli.commands.run.run`) delegate to a single helper
`ralph.interrupt.handle_keyboard_interrupt_at_cli`, which is the canonical
owner of the `block=True` + exit-code-130 contract AND the
escalation-when-stuck behavior. When the grace deadline expires with active
records still present, the dispatcher escalates via `force_exit` (root-cause
fix for the 'frozen pipeline after Ctrl+C' failure mode). Both call sites
use the same helper so a regression in one is a regression in both. The
helper is black-box tested in `tests/test_interrupt_cli_helper.py` with a
fake clock and fake process manager — no real wall-clock waits.

## InterruptDispatcher — the single seam

The `InterruptDispatcher` in `ralph.interrupt.dispatcher` is the single seam
that wires `InterruptController` to `ProcessManager`, the connectivity-stop
callback, and the hard-exit function. Both the sync `handle_keyboard_interrupt`
path (`ralph.pipeline._runner_interrupt`) and the asyncio path
(`ralph.interrupt.asyncio_bridge.install_signal_handlers`) build their
dispatchers through the same factory `dispatcher_from_process_manager`, so any
future change to the wiring happens in one place. The CLI KeyboardInterrupt
catches in `ralph.cli.commands.run` and `ralph.cli.main` also call
`dispatcher.begin_interrupt(block=True)` before returning exit code 130, so
the agent process group is SIGTERMed even when the interrupt propagates
outside the pipeline loop. The dispatcher's `hard_kill_budget_s` and
`poll_interval_s` fields are the source of truth for the early-escalation
timing; the module-level constants in `_runner_interrupt` are kept only for
backward compatibility (re-exported from `ralph.interrupt.dispatcher`).

The CLI-level entry point `ralph.interrupt.handle_keyboard_interrupt_at_cli`
sits alongside the dispatcher factory as the canonical seam for the CLI
catch path. It builds an `InterruptDispatcher` via the factory, calls
`begin_interrupt(grace_period_s=..., block=True)`, and returns the canonical
exit code (130 by default, or the override supplied). When the grace
deadline elapses with active records still present, the helper's underlying
dispatcher escalates via `force_exit` so the frozen-pipeline-after-Ctrl+C
failure mode is no longer silent. The helper is the single owner of the
CLI catch contract; both call sites use the same function so a regression
in one is a regression in both.

## Long-running task interruption

**Symptom:** When the agent runs for many minutes, pressing `Ctrl+C`
once may not appear to interrupt the run immediately. The first
SIGINT begins the graceful shutdown; a second SIGINT force-kills.

**Cause:** The first SIGINT starts a two-phase shutdown: graceful
SIGTERM via `shutdown_all_for_label`, followed by the
`_wait_for_list_active_empty` block (which polls until the tracked
process group drains OR the grace deadline expires). On multi-minute
runs, the pipeline may still be in the middle of `begin_interrupt`'s
body when the user presses `Ctrl+C` a second time, which is why a
single `Ctrl+C` can look like it was ignored — the body is in flight.

A second `Ctrl+C` is a hard escalation: the second-SIGINT handler
reads `pm.list_active()` PGIDs and calls `dispatcher.force_exit`
directly, which SIGKILLs the active records and exits with code 130
(even if the first-SIGINT executor body is still running). This is
the design: the first SIGINT is cooperative, the second SIGINT is
authoritative.

### What if the process refuses to die?

If the agent's process group ignores SIGTERM, the dispatcher's
`_wait_for_list_active_empty` escalates via `self.force_exit(bridge_pgids=...)`
when the grace deadline expires with active records still present.
The escalation is idempotent: a subsequent `force_exit` (e.g. from
a second SIGINT, or from the body eventually completing and trying
to escalate again) is a no-op. The `hard_exit` callable is invoked
exactly once across the two SIGINTs and the body's eventual
completion, so a frozen pipeline is never silent.

The two long-running-task invariants — (1) second SIGINT while the
first-SIGINT executor body is still in flight, and (2) slow
`begin_interrupt` body that takes longer than the grace period to
return — are pinned by black-box tests in
`tests/test_asyncio_bridge_install_signal_handlers.py` and
`tests/test_interrupt_dispatcher.py`. The SYNC-path equivalent
(`tests/test_runner_interrupt.py::test_second_sigint_during_first_sigint_interrupt_thread`)
pins the same contract for the `handle_keyboard_interrupt` entry
point that a real Ctrl+C reaches inside the pipeline loop on
unattended runs. The full architectural
contract (controller / dispatcher split, clock + sleep seams,
PGID routing, Strategy A propagation, long-running-body
idempotency, `run_shutdown_block` seam) is documented in
[`adr-0001-interrupt-architecture`](../../architecture/adr-0001-interrupt-architecture.md)
sections D1–D8.

### Entry-point testability — the `handle_keyboard_interrupt` seams

The sync entry point
`ralph.pipeline._runner_interrupt.handle_keyboard_interrupt` is the
seam a real Ctrl+C reaches inside the pipeline loop. It is now
black-box testable end-to-end via the minimum seam surface of two
new kwargs and one guard. The entry point accepts:

- `process_manager: ProcessManager | None = None` — replaces the
  hard-coded `get_process_manager()` singleton. Production callers
  omit the kwarg and the singleton is used; tests inject a fake.
- `poll_interval_s: float = 0.05` — replaces the literal `0.05` in
  the `while not interrupt_done.wait(timeout=...)` busy-wait. The
  default is unchanged from production behavior; tests inject
  `0.001` so the busy-wait returns in <1ms. Clock and sleep seams
  are intentionally NOT added because the entry point only uses
  `threading.Event` coordination, not `time.monotonic()` or
  `time.sleep()` — the dispatcher's clock + sleep seams are
  sufficient for the timing tests.
- A `RuntimeError` is raised when both a pre-built `dispatcher` and
  a `monitor_stop` callable are passed. The prior silent-ignore
  was a footgun that hid a real contract violation. Callers that
  previously relied on the silent-ignore behavior must now thread
  `monitor_stop` only when `dispatcher is None`.

The 6 black-box tests in `tests/test_runner_interrupt.py` pin
these contracts: the `poll_interval` seam, the `RuntimeError`
guard, the recovery from non-fatal dispatcher failures (the
`except Exception` recovery block in `_begin_interrupt`), the
first-SIGINT contract, the second-SIGINT contract, and the
`_NotAnException(BaseException)` discriminator that proves the
`Exception`-not-`BaseException` change. The 4 black-box tests in
`tests/pipeline/test_run_loop_interrupt.py` pin the
`run_loop._handle_keyboard_interrupt` wrapper. See
[`adr-0001-interrupt-architecture`](../../architecture/adr-0001-interrupt-architecture.md)
D5 and D6.

Both the SYNC entry point (`handle_keyboard_interrupt`) and the
asyncio entry point (`install_signal_handlers`) route through the
shared `ralph.interrupt.dispatcher.run_shutdown_block` helper, so
the first-SIGINT shutdown block (begin_interrupt + early-escalation
poll in a daemon thread + bounded join) cannot drift between the
two paths. The 7th architectural seam — `error_log_message` — is
the only delta between them: the SYNC path passes
`"Interrupt controller raised during KeyboardInterrupt"` and the
asyncio path passes `"Interrupt shutdown block raised"`.

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
