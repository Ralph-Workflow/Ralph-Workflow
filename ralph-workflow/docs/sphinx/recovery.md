# Recovery

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Ralph Workflow treats failure recovery as a first-class concern. Most recovery behavior is
built in and automatic: the pipeline retries transient failures, pauses safely during
connectivity problems, and keeps checkpoint state up to date as it runs.

In normal use, you do **not** need to do anything special to "turn on" recovery. The main
manual choice is whether to resume from a saved checkpoint with `ralph --resume` or start
fresh instead.

## Failure categories

Every failure is classified into one of four categories:

| Category | Description | Counts against budget? |
|----------|-------------|----------------------|
| `environmental` | Network outage, upstream service error, transport disconnect | No — retries are free |
| `agent` | Empty output, idle timeout, malformed tool calls, repeated policy violations | Yes |
| `user_config` | Invalid config, unbound agent chain, missing required inputs | No — pre-flight should catch these |
| `ambiguous` | Cannot determine cause | No — flagged for review, counted in recovery cycles |

Attribution is intelligent: a re-prompt caused by a brief outage does not cost the agent a
retry; an empty-output timeout does. Ambiguous errors default to the safer retry path.

## Offline detection and auto-resume

Ralph Workflow actively monitors connectivity. While offline, the pipeline pauses — it makes
no progress rather than burning budget or failing noisily. Once connectivity returns, the
pipeline resumes automatically and re-prompts the affected iteration without counting the
outage against any agent. You will see:

```
Offline — paused (since HH:MM:SS)
```

When connectivity is restored:

```
Recovery resumed after offline
```

## Two-SIGINT contract

- **First Ctrl+C**: cancels in-flight work, triggers ordered shutdown (kills subprocesses,
  saves checkpoint), then pauses. The pipeline can be resumed.
- **Second Ctrl+C**: exits immediately with no cleanup.

## Recovery-cycle cap

The `[general].max_cycles` setting (default: `3`) limits the total number of full-chain
exhaustion recovery cycles. When the cap is reached, the pipeline exits with a descriptive
error showing the cap value and the last failure category. This prevents a
persistently-failing handler from looping silently forever.

Configure it in `ralph-workflow.toml` (or `.agent/ralph-workflow.toml`):

```toml
[general]
max_cycles = 3   # default: 3
```

Per-chain retry behavior comes from the active agent policy synthesized from
`ralph-workflow.toml`. Only after all agents in a chain are exhausted does the
recovery cycle count increment.

## Agent chain fallover

Each phase uses an agent chain (for example, `claude/opus → opencode/minimax/MiniMax-M2.7-highspeed → claude/sonnet`). When an agent exhausts its
`max_retries` budget, Ralph Workflow falls over to the next agent in the chain with a clean
state — no silent retries, no double-counting. Chain composition is validated pre-flight.

## Child agent liveness classification

Ralph Workflow distinguishes four child-agent liveness states based on fresh evidence rather
than process existence alone:

- **Active progressing** — child emitted a progress signal within `agent_child_progress_ttl_seconds` (default 45 s).
- **Alive but quiet** — child process or label exists but no recent progress; within grace threshold.
- **Hung or stale** — child process or label exists, but progress and heartbeat leases have expired.
  Ralph Workflow stops waiting and escalates to a resumable-retry path rather than holding
  `WAITING_ON_CHILD` open indefinitely.
- **Exited confirmed** — child emitted an explicit terminal acknowledgement, or no fresh evidence
  remains and no OS descendants are present.

The key implication: **raw process existence no longer implies active child work.**
A stale label or PID that has not renewed its progress lease within the configured TTL
will not keep the parent waiting. Progress and heartbeat signals, not process presence,
drive the liveness decision.

Waiting status log lines include an `alive_by=` key that explains why WAITING_ON_CHILD
is held open:

- `alive_by=fresh_progress` — progress renewed within TTL
- `alive_by=fresh_heartbeat_only` — heartbeat renewed but progress stale
- `alive_by=os_descendant_only_stale_progress` — OS-level descendant only; registry is stale
- `alive_by=stale_label_only` — label present but stale (warn-worthy; may escalate)

## No-progress child wait ceiling

When a child agent is alive but not making forward progress (heartbeat-only, stale-label, or
OS-descendant-only evidence), Ralph Workflow applies a shorter **no-progress ceiling** instead
of the full `agent_idle_max_waiting_on_child_seconds` ceiling. This prevents a stuck child
from holding `WAITING_ON_CHILD` open for the full 1800 s when it is clearly not doing work.

The no-progress ceiling is configured via `agent_idle_no_progress_waiting_on_child_seconds`
(default: 600 s). It must be less than or equal to `agent_idle_max_waiting_on_child_seconds`.
Set it to `null` to disable the no-progress ceiling entirely and always use the full ceiling.

The effective ceiling in use is visible in the HARD_STOP diagnostic as `effective_ceiling`:
- `effective_ceiling=no_progress` — shorter no-progress ceiling fired
- `effective_ceiling=standard` — full ceiling fired

Example TOML configuration:

```toml
[general]
agent_idle_max_waiting_on_child_seconds = 1800.0  # full ceiling
agent_idle_no_progress_waiting_on_child_seconds = 600.0  # no-progress ceiling (default)
# agent_idle_no_progress_waiting_on_child_seconds = null  # disable no-progress ceiling
```

## Idle activity and session safety

Idle timeout is based on transport activity signals, not only visible transcript text.
Provider lifecycle events, streaming deltas, tool calls, and tool results can reset the idle
watchdog even when the display parser later suppresses them as non-user-facing noise.
Whitespace-only output is not activity, so blank heartbeats cannot keep a stuck subprocess
alive indefinitely.

When Ralph Workflow forcibly terminates a subprocess for inactivity, the next retry starts a
fresh agent session by default. Captured session IDs from a killed process are treated as
unsafe unless that transport explicitly marks resume after forced termination as safe. If a
provider reports a stale session error such as `No conversation found with session ID`,
`Session not found`, or `Unknown session`, recovery also retries fresh within the remaining
budget even if the failed output included another session ID.

## How to read failure events in logs

Failure events are emitted as structured log entries with `recovery=true`:

```
2026-04-21 12:00:00 | DEBUG    | ralph.recovery | category=environmental phase=development agent=claude counted=False
2026-04-21 12:00:05 | INFO     | ralph.recovery | category=agent phase=development agent=claude counted=True
2026-04-21 12:00:10 | DEBUG    | ralph.recovery | category=fallover phase=development from_agent=claude to_agent=opencode
```

## Configuration knobs

Agent chain retry budget and backoff are normally configured in `ralph-workflow.toml`:

```toml
# .agent/ralph-workflow.toml
[general]
max_retries = 3
retry_delay_ms = 1000

[agent_chains]
development = ["claude", "opencode/minimax/MiniMax-M2.7-highspeed"]
```

The maximum fallback cycles through a drain is also configured in `ralph-workflow.toml`:

```toml
# ralph-workflow.toml or .agent/ralph-workflow.toml
[general]
max_cycles = 3   # max full fallback cycles through a drain (default: 3)
```

`retry_delay_ms` controls the base delay between retries for agent-attributable failures.
The delay uses exponential backoff: each retry doubles the delay (base_ms × 2^attempt),
capped at 30 seconds. For example, with `retry_delay_ms = 1000`:

- Retry 1: 1 s delay
- Retry 2: 2 s delay
- Retry 3: 4 s delay
- Subsequent retries: capped at 30 s

Environmental and ambiguous failures always retry with 0 delay (immediately). The delay
resets to base after a successful agent invocation or a chain fallover to the next agent.

Connectivity probe interval can be configured in code via `ConnectivityMonitor(probe_interval_s=10.0)`.

## Missing plan handoff during recovery

When the pipeline is in the `failed_route` recovery phase and tries to re-enter a non-planning phase, prompt materialization checks for `.agent/PLAN.md` (or a `plan.json` artifact it can regenerate from). If neither exists — for example because a fresh checkout or partial reset removed the plan files — the runner intercepts the `MissingPlanHandoffError`, logs a warning, and reroutes the state back to the pipeline entry phase (usually `planning`) instead of crashing with an ambiguous terminal error.

The reroute increments `recovery_epoch` and stores the error in `last_error`, so the checkpoint reflects the new state. The next pipeline step then begins a fresh planning pass from scratch.

This reroute applies **only** inside the `failed_route` recovery path. If a non-recovery execution phase raises `MissingPlanHandoffError`, the exception propagates normally so the genuine contract violation remains visible.

## Checkpoints

Ralph Workflow saves a checkpoint after each phase completes so the pipeline can resume
from exactly where it left off after an interruption or crash.

Checkpoint **writing** is automatic. **Using** a saved checkpoint is an explicit operator
choice: pass `ralph --resume` when you want to continue from the saved state.

### Where the checkpoint lives

```
.agent/checkpoint.json
```

The file is written atomically (write to `.agent/checkpoint.json.tmp`, then rename) to
prevent partial-write corruption. The `.agent/` directory is created automatically if it
does not exist.

### What is stored

The checkpoint contains:

- **Current phase** — the last successfully completed phase name
- **Plan artifact reference** — path to the plan artifact so development can reload context
- **Iteration counts** — how many developer and reviewer iterations have been consumed
- **Last error** — the most recent failure message (if any) for diagnostic display

### When checkpoints are written

A checkpoint is written after every successful phase transition. If the pipeline is
interrupted mid-phase, the checkpoint reflects the last *completed* phase, not the
in-progress one — the interrupted phase is retried from scratch on resume.

### Resuming from the checkpoint

```bash
ralph --resume
```

This tells Ralph Workflow to load `.agent/checkpoint.json` and continue from the last
completed phase. If no checkpoint exists, Ralph Workflow prints a warning and starts a
fresh run instead.

### Inspecting the checkpoint

```bash
ralph --inspect-checkpoint
```

Prints the checkpoint contents as formatted JSON. Use this to confirm what phase will be
resumed before running `ralph`.

### Ignoring the checkpoint

```bash
ralph --no-resume
```

Starts the pipeline from the beginning, ignoring any existing checkpoint. The checkpoint
file is not deleted; it is simply skipped.

### Enabling or disabling checkpoints

In `ralph-workflow.toml` (or `.agent/ralph-workflow.toml`):

```toml
[general.workflow]
checkpoint_enabled = true   # set false to disable checkpoint writes entirely
```

When `checkpoint_enabled = false`, the pipeline runs without writing any checkpoint and
will always restart from the beginning regardless of prior state.

## Related pages

- [Concepts](concepts.md) — phase, drain, checkpoint, and recovery cycle terminology
- [Parallel Mode](parallel-mode.md) — recovery behavior in same-workspace parallel runs
- [Troubleshooting](troubleshooting.md) — common recovery-related issues and fixes
