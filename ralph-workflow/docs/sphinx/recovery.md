# Recovery

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Ralph Workflow treats failure recovery as a first-class concern. The pipeline is designed
to keep running through transient failures, preserve enough context to resume cleanly, and
only terminate on user intent or pre-flight validation errors.

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

A global `recovery_cycle_cap` (default: 200) bounds the total number of full-chain exhaustion
recovery cycles. When exceeded, the pipeline exits with a descriptive error referencing the cap
value and the last failure. This prevents a persistently-failing handler from looping silently
forever.

## Agent chain fallover

Each phase uses an agent chain (e.g., `claude → opencode`). When an agent exhausts its
`max_retries` budget, Ralph Workflow falls over to the next agent in the chain with a clean
state — no silent retries, no double-counting. Chain composition is validated pre-flight.

## How to read failure events in logs

Failure events are emitted as structured log entries with `recovery=true`:

```
2026-04-21 12:00:00 | DEBUG    | ralph.recovery | category=environmental phase=development agent=claude counted=False
2026-04-21 12:00:05 | INFO     | ralph.recovery | category=agent phase=development agent=claude counted=True
2026-04-21 12:00:10 | DEBUG    | ralph.recovery | category=fallover phase=development from_agent=claude to_agent=opencode
```

## Configuration knobs

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

## Checkpoints

Ralph Workflow saves a checkpoint after each phase completes so the pipeline can resume
from exactly where it left off after an interruption or crash.

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
- [Parallel Mode](parallel-mode.md) — recovery behavior in parallel worktree runs
- [Troubleshooting](troubleshooting.md) — common recovery-related issues and fixes
