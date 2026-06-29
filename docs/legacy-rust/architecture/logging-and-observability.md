# Logging and Observability Architecture

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


## Overview

Ralph's logging infrastructure provides comprehensive observability into pipeline execution through a per-run directory structure. All logs from a single `ralph` invocation are grouped under `.agent/logs-<run_id>/`, making it easy to:

- Share logs as a cohesive artifact
- Correlate logs to a specific run (including across `--resume`)
- Inspect event loop behavior without reconstructing control flow
- Debug pipeline issues with complete context

## Per-Run Log Directory Structure

Each run creates exactly one directory at:

```
.agent/logs-<run_id>/
```

Where `<run_id>` is a UTC timestamp with millisecond precision in the format:

```
YYYY-MM-DD_HH-mm-ss.SSSZ[-NN]
```

The optional `-NN` suffix (e.g., `-01`, `-02`) handles the rare case of multiple runs starting in the same millisecond.

### Directory Layout

```
.agent/
  logs-<run_id>/
    run.json                    # Run metadata (required)
    pipeline.log                # Pipeline execution log (required)
    event_loop.log              # Event loop observability log (required)
    event_loop_trace.jsonl      # Crash-only trace dump (optional)
    agents/                     # Per-agent invocation logs
      planning_1.log
      dev_1.log
      dev_1_a1.log              # Retry attempt
      reviewer_1.log
      commit_1.log
    provider/                   # Provider streaming logs (future)
      claude-stream_dev_1.jsonl
    debug/                      # Future debug artifacts
```

### Run ID Format

The run ID is designed to be:

- **Human-readable**: Clear timestamp format
- **Machine-sortable**: Lexicographic sort == chronological order
- **Filesystem-safe**: No colons; works on macOS, Linux, Windows

Examples:
- `2026-02-06_14-03-27.123Z` (base format)
- `2026-02-06_14-03-27.123Z-01` (with collision counter)

### Collision Handling

If a run directory already exists (e.g., two runs started in the same millisecond), the system appends a zero-padded collision counter:

```rust
// First collision: .agent/logs-2026-02-06_14-03-27.123Z-01/
// Second collision: .agent/logs-2026-02-06_14-03-27.123Z-02/
```

This ensures:
- No overwrites or data loss
- Chronological sorting is preserved within the same millisecond
- Maximum of 99 collisions supported per millisecond

## Run Metadata (run.json)

Each run directory includes a metadata file providing context for debugging and tooling.

### Required Fields

```json
{
  "run_id": "2026-02-06_14-03-27.123Z",
  "started_at_utc": "2026-02-06T14:03:27.123Z",
  "command": "ralph",
  "resume": false,
  "repo_root": "/absolute/path/to/repo",
  "ralph_version": "0.6.3"
}
```

### Optional Fields

```json
{
  "pid": 12345,
  "config_summary": {
    "developer_agent": "claude",
    "reviewer_agent": "claude",
    "total_iterations": 3,
    "total_reviewer_passes": 1
  }
}
```

### When Metadata is Written

Run metadata is written early in pipeline execution (during initialization) to ensure it's available even if the run fails early. The metadata anchors debugging with essential context about how Ralph was invoked.

## Log Types

### Pipeline Log (pipeline.log)

The main execution log containing:
- Phase transitions
- Agent invocations
- Key decisions (retries, fallbacks, continuations)
- User-facing progress messages

**Path**: `.agent/logs-<run_id>/pipeline.log`

**Format**: Human-readable text with timestamps

**Behavior**: Appended on resume (never overwritten)

### Event Loop Log (event_loop.log)

An always-on observability log recording the event loop's progression:

- Which effects ran
- What events were emitted
- Phase/iteration/retry context
- Handler wall time

**Path**: `.agent/logs-<run_id>/event_loop.log`

**Format**: Structured text, one line per effect

**Line Structure**:
```
<seq> ts=<rfc3339> phase=<Phase> effect=<Effect> event=<Event> [extra=[E1,E2]] [ctx=k1=v1,k2=v2] ms=<N>
```

Note: The `ctx` field shows key-value pairs without brackets (e.g., `ctx=k1=v1,k2=v2`), not `[ctx=k1=v1,k2=v2]`.

**Example**:
```
1 ts=2026-02-06T14:03:27.123Z phase=Development effect=InvokePrompt event=PromptCompleted ms=1234
2 ts=2026-02-06T14:03:28.456Z phase=Development effect=WriteFile event=FileWritten ctx=file=PLAN.md ms=12
```

**Redaction Requirements**:
- Must never include full prompt contents
- Must never include model outputs
- Must never include git diffs
- Must never include secrets/tokens/credentials
- Errors must be sanitized (message only, no embedded payloads)

### Event Loop Trace (event_loop_trace.jsonl)

A bounded ring buffer snapshot written only on:
- Internal failure
- Iteration cap reached
- Unrecoverable handler errors
- Panics

**Path**: `.agent/logs-<run_id>/event_loop_trace.jsonl`

**Format**: NDJSON (newline-delimited JSON)

**Behavior**: Only written on failure/iteration-cap (not during normal execution)

### Agent Invocation Logs

Per-phase, per-agent invocation logs with simplified naming.

**Path**: `.agent/logs-<run_id>/agents/<phase>_<index>[_aN].log`

**Naming Convention**:
- First attempt: `<phase>_<index>.log` (e.g., `planning_1.log`, `dev_1.log`)
- Retry attempts: `<phase>_<index>_aN.log` (e.g., `dev_1_a1.log`, `dev_1_a2.log`)

**Log Header**: Each agent log includes a header with:
```
# Ralph Agent Invocation Log
# Role: Development
# Agent: claude
# Model Index: 0
# Attempt: 0
# Phase: Development
# Timestamp: 2026-02-06T14:03:27.123Z
```

**Rationale**: Agent identity is recorded in the log header (not the filename) because logs are already grouped per-run. This simplifies filename management while preserving all necessary metadata.

### Provider Logs (future)

Provider streaming artifacts (NDJSON/JSONL capture) will be written under:

```
.agent/logs-<run_id>/provider/<provider>-stream_<phase>_<index>.jsonl
```

**Status**: Infrastructure exists but not yet used in production.

## Resume Semantics

### Fresh Run

1. Generate new `run_id` with current UTC timestamp
2. Create run log directory (`.agent/logs-<run_id>/`)
3. Write `run.json` with `resume: false`
4. All logs written to new run directory

### Resume (`--resume`)

1. Load checkpoint (`.agent/checkpoint.json`)
2. Extract `run_id` from checkpoint
3. Continue using same run log directory (`.agent/logs-<run_id>/`)
4. Append to existing logs (`pipeline.log`, `event_loop.log`)
5. Write `run.json` with `resume: true` (if missing or updating metadata)

### Legacy Resume (from old checkpoint format)

If resuming from a checkpoint without `run_id`:
1. Generate new `run_id`
2. Record in `run.json` that this is a resume-from-legacy run
3. Continue with new run directory

**Note**: Directory recreation is automatic if deleted (preserves run_id).

## Canonical Orchestrator Artifacts (Not Moved)

The following files remain in their original locations (not under the run log directory):

- `.agent/PLAN.md` - Implementation plan
- `.agent/ISSUES.md` - Code review issues
- `.agent/STATUS.md` - Pipeline status
- `.agent/NOTES.md` - Additional notes
- `.agent/commit-message.txt` - Generated commit message
- `.agent/checkpoint.json` - Checkpoint for resume
- `.agent/tmp/*.xml` - XSD validation scratch files

**Rationale**: These files are correctness-critical artifacts used by the reducer/orchestrator, not observability logs. They must remain in stable, well-known locations for the pipeline to function correctly.

## Idle Timeout Activity Detection

Idle-timeout decisions use a **three-layer** model to avoid false positive timeouts for active runs while still terminating stuck agents promptly.

### Layer 0: Per-Channel Activity Evidence

The watchdog tracks four independent evidence channels and consults the
per-channel timestamps on every evaluate() call:

| Channel | Source | Recorder (where invoked) |
|---|---|---|
| `stdout` | Agent stdout output (the baseline) | `IdleWatchdog.record_activity()` / `record_lifecycle_activity()` |
| `mcp_tool` | Ralph Workflow MCP `tools/call` invocations and completions | `IdleWatchdog.record_mcp_tool_call()` (invoked from the MCP server's tools/call dispatch path `_handle_tools_call` via the module-level helper `ralph.mcp.server._activity_sink.invoke_active_sink`) |
| `subagent` | Delegated child progress / heartbeat / tool-call signals | `IdleWatchdog.record_subagent_work()` (invoked from `OpenCodeExecutionStrategy.observe_line` via `ralph.mcp.server._activity_sink.invoke_subagent_sink` on `CHILD_PROGRESS` and `CHILD_HEARTBEAT` signals) |
| `workspace` | Workspace file change events from `WorkspaceMonitor` | `IdleWatchdog.record_workspace_event()` (invoked from `WorkspaceMonitor.record_event` after the late `set_on_event` binding installed by the PTY/process readers' `read_lines` generators) |

The three non-stdout recorders update per-channel `_last_at` timestamps
and counters WITHOUT touching `_last_activity` (the stdout baseline);
the existing 'stdout only resets idle baseline' invariant is preserved.

evaluate()'s control flow: after the SESSION_CEILING and
REPEATED_ERROR_LOOP early-exit checks, the watchdog checks the drain
window, then the post-tool-result wedge, then `classify_quiet()` to
choose between the WAITING_ON_CHILD branch (which may fire
CHILDREN_PERSIST_TOO_LONG) and the ACTIVE branch (which may fire
NO_OUTPUT_DEADLINE after the drain window). Layer 0's deferral is
consulted INSIDE the ACTIVE branch ONLY: when the idle deadline has
elapsed, the drain window has elapsed, the post-tool-result wedge has
not fired, AND `classify_quiet()` returns `ACTIVE` (no children
visible), the verdict hook consults
`IdleWatchdog._channel_evidence_active(now)`. If ANY non-stdout
channel is fresher than `agent_idle_activity_evidence_ttl_seconds`
(default 30.0s, tunable in `ralph-workflow.toml`; `0.0` or `None`
disables the feature and restores the legacy stdout-only behavior),
the watchdog defers the NO_OUTPUT_DEADLINE fire and returns
`WatchdogVerdict.CONTINUE` with a debug log naming the active channel
and its age. The four channels are independent: a session that is
quiet on stdout but actively making MCP tool calls, delegating to a
live subagent, or writing files is not killed as idle. The opposite
is also true: a subagent that exists but has produced no tool calls,
no progress signals, and no file changes for the configured TTL is
NOT evidence of progress — the channel goes stale and the watchdog
returns to the regular idle path.

The SESSION_CEILING and CHILDREN_PERSIST_TOO_LONG ceilings are checked
BEFORE the per-channel deferral (CHILDREN_PERSIST_TOO_LONG inside its
own branch, SESSION_CEILING in the early-exit block), so activity
cannot reset or extend either ceiling. The absolute ceiling contract
is preserved.

Diagnostic scope: every reader-level fire diagnostic
(`_IdleStreamTimeoutError.diagnostic` raised by
`_pty_line_reader._check_fire` and `_process_reader._check_fire`)
embeds the per-channel `evidence_summary` from
`watchdog.last_evidence_summary(now)`, so an on-call operator or
post-mortem reader can see exactly which channels were fresh and
which were stale at the moment the fire was raised — regardless of
which branch produced it. The CHILDREN_PERSIST_TOO_LONG HARD_STOP
event also embeds the per-channel evidence_summary via
`_build_evidence_summary_diag` so listener subscribers see the same
surface. The watchdog's own `log.warning()` lines (in `evaluate()`
and the branch handlers) include the relevant scalar context
(idle_elapsed, session_elapsed, cumulative_waiting, channel age on
the deferral path) but do not embed the full per-channel
evidence_summary in the log line. See `## Idle watchdog — per-channel
activity evidence model` in `docs/agents/verification.md` for the
operator-facing description and the test coverage in
`ralph-workflow/tests/agents/test_idle_watchdog_3.py` (21 cases),
`ralph-workflow/tests/mcp/test_mcp_activity_sink.py` (7 cases), and
`ralph-workflow/tests/agents/test_subagent_activity_wiring.py`
(7 cases).

### Layer 1: Output Idle Deadline

The first layer tracks time since the last meaningful output line from the agent.
If no output arrives within `agent_idle_timeout_seconds` (default **300 seconds**), the watchdog calls `classify_quiet()` to determine child process state:

- **`WAITING_ON_CHILD`**: live child processes are active — enter the waiting branch (Layer 2).
- **`ACTIVE`** (no children): enter a short drain window (`agent_idle_drain_window_seconds`, default **0.5s**), then fire `NO_OUTPUT_DEADLINE`.

If `classify_quiet()` raises, the watchdog defaults conservatively to `WAITING_ON_CHILD` to avoid false-positive termination during transient probe failures.

### Layer 2: Child-Liveness Evidence and Waiting Ceilings

When children are present, the watchdog evaluates child-liveness *evidence quality* to distinguish genuine forward progress from lifecycle noise:

| `alive_by` value | Meaning | Effective ceiling |
|---|---|---|
| `fresh_progress` | Child produced a progress signal within `child_progress_ttl` | Full ceiling (1800s) |
| `fresh_heartbeat_only` | Heartbeat present but no progress renewal | No-progress ceiling (600s) |
| `stale_label_only` | Label persists after freshness expires | No-progress ceiling (600s) |
| `os_descendant_only_stale_progress` | OS process exists but no fresh registry evidence | No-progress ceiling (600s) |

**The no-progress ceiling** (`agent_idle_no_progress_waiting_on_child_seconds`, default **600 seconds**) fires `CHILDREN_PERSIST_TOO_LONG` when cumulative WAITING_ON_CHILD time exceeds it and the child is alive but not making forward progress. This catches stuck agents that keep OS descendants alive via heartbeat noise alone without producing real work.

**The full waiting ceiling** (`agent_idle_max_waiting_on_child_seconds`, default **1800 seconds**) applies only when the child has verifiably fresh progress. This prevents false positives for genuinely long-running child work.

Both ceilings are **absolute** (cumulative across the session) and cannot be reset by oscillating between active and waiting states.

### Corroboration Snapshot

On each watchdog tick, one corroboration snapshot is captured and reused for:
1. Selecting the effective ceiling (no-progress vs. full).
2. Emitting suspicion diagnostics (`SUSPECTED_FROZEN` event).
3. Emitting hard-stop diagnostics (`HARD_STOP` event) on fire.

Reusing a single snapshot per tick prevents divergence between the ceiling decision and the diagnostic fields logged with it.

### Session Ceiling

The absolute session wall-clock ceiling (`agent_max_session_seconds`, default disabled) fires `SESSION_CEILING_EXCEEDED` regardless of activity. It is checked first on every watchdog tick and cannot be defeated by child deferral.

### Single Source of Truth for Defaults

All numeric timeout and child-liveness defaults are defined once in `ralph/timeout_defaults.py` and imported by:
- `ralph.agents.idle_watchdog.TimeoutPolicy` (runtime policy)
- `ralph.agents.invoke` (child-liveness TTL fallbacks)
- `ralph.config.models.GeneralConfig` (config field defaults)

Changing a constant in `timeout_defaults.py` propagates to all three layers automatically.

### Timeout Decision Logging

When a timeout fires, the watchdog logs:
- Fire reason: `NO_OUTPUT_DEADLINE`, `CHILDREN_PERSIST_TOO_LONG`, or `SESSION_CEILING_EXCEEDED`.
- `cumulative_waiting` seconds and `idle_elapsed` seconds.
- For `CHILDREN_PERSIST_TOO_LONG`: `alive_by` evidence, `effective_ceiling` classification (`standard` vs. `no_progress`), `scoped_child_active`, and `workspace_event_delta`.

## Architecture Integration

### Reducer/Effect Boundary

Per-run logging strictly follows Ralph's reducer-driven architecture:

- **Reducers remain pure**: No logging, no time access, no filesystem I/O
- **Orchestrators remain pure**: No logging; they only choose the next `Effect`
- **All I/O stays inside effect handlers**: The event loop driver and effect handlers are the only writers

### RunLogContext

The `RunLogContext` struct is created once per run in the impure layer (effect-handling layer) and passed to all effect handlers. It:

- Owns the `run_id`
- Resolves run-relative paths (e.g., `pipeline.log`, `agents/...`, `event_loop.log`)
- Uses `Workspace` trait for filesystem operations (no `std::fs` in pipeline layer)
- Ensures directory creation is explicit (via early effect or dedicated "ensure logging" effect)

### Event Loop Integration

The event loop driver emits `event_loop.log` entries *after* each effect handler returns an `EffectResult`:

```rust
// Pseudocode
let start = Instant::now();
let result = handler.handle(effect, ctx);
let duration_ms = start.elapsed().as_millis();

event_loop_logger.log_effect(LogEffectParams {
    phase: state.phase,
    effect: effect_name,
    primary_event: result.primary_event,
    extra_events: result.extra_events,
    duration_ms,
    context: build_context(&state),
});
```

This ensures the log reflects the actual (effect → events) boundary defined by the architecture.

## Error Handling

### Run Directory Creation Failure

If the run log root cannot be created, Ralph must:
- Fail early with a clear error message
- Include attempted path and underlying OS error
- Not attempt to fall back to legacy locations

### Individual Log Write Failures

During execution, individual log write failures should:
- Be reported to the pipeline log (best-effort)
- Not corrupt pipeline correctness (the pipeline should continue when safe)
- Use `Workspace::append_bytes()` for append-only operations

### Trace Dump Failures

If event loop trace dump fails:
- Log the error to pipeline log
- Continue execution (trace is observability, not correctness)

## Performance Considerations

- `event_loop.log` writes are append-only and O(1) per loop iteration
- Logging should not meaningfully change runtime for typical runs
- Avoid serializing large state (effect names and event names only)
- Use bounded ring buffer for trace (not unbounded growth)

## Backward Compatibility

### Migration from Legacy Logs

- New versions stop writing logs to `.agent/logs/`
- Tooling/tests that read `.agent/logs/pipeline.log` must locate the current run's log via:
  - The checkpoint's `run_id` field
  - Optional pointer file (`.agent/current_run.txt`) containing `run_id`

### Agent Log Naming Migration

Existing agent log filename conventions that embedded agent/model identity are replaced by simplified per-run names. Identity metadata is recorded in log file headers instead.

## Tooling Integration

### Finding Current Run Logs

**Option 1: Via Checkpoint**
```bash
RUN_ID=$(jq -r .run_id .agent/checkpoint.json)
PIPELINE_LOG=".agent/logs-${RUN_ID}/pipeline.log"
```

**Option 2: Via Current Run Pointer (if implemented)**
```bash
RUN_ID=$(cat .agent/current_run.txt)
PIPELINE_LOG=".agent/logs-${RUN_ID}/pipeline.log"
```

**Option 3: Lexicographically Latest**
```bash
LATEST_RUN=$(ls -1d .agent/logs-* | sort | tail -n1)
PIPELINE_LOG="${LATEST_RUN}/pipeline.log"
```

### Sharing Logs

To share logs for a specific run:
```bash
tar -czf logs.tar.gz .agent/logs-<run_id>/
```

All logs from that run are in a single directory, making sharing trivial.

### Analyzing Event Loop Behavior

```bash
# Count effects by type
grep -oP 'effect=\K\w+' .agent/logs-<run_id>/event_loop.log | sort | uniq -c

# Find slow effects (>1000ms)
awk '$NF ~ /^ms=/ && substr($NF, 4) > 1000' .agent/logs-<run_id>/event_loop.log

# Track phase transitions
grep -oP 'phase=\K\w+' .agent/logs-<run_id>/event_loop.log | uniq
```

## Testing

### Integration Tests

- `tests/integration_tests/logging_per_run.rs`: Per-run logging infrastructure
  - Run directory format and collision handling
  - Resume continuity
  - Event loop log structure
  - Redaction requirements
  - No legacy logs created
  - Agent log headers

- `tests/integration_tests/event_loop_trace_dump.rs`: Event loop trace dump

### Unit Tests

- `ralph-workflow/src/logging/run_log_context.rs`: RunLogContext path resolution
- `ralph-workflow/src/logging/run_id.rs`: RunId format and collision counter
- `ralph-workflow/src/logging/event_loop_logger.rs`: EventLoopLogger formatting

## Agent Timeout Architecture

### Single Source of Truth

All timeout and child-liveness numeric defaults live in `ralph/timeout_defaults.py`.
This module is the single source of truth imported by:

- `ralph.agents.idle_watchdog.TimeoutPolicy` (dataclass field defaults)
- `ralph.agents.invoke` (child-liveness TTL constants)
- `ralph.config.models.GeneralConfig` (config field defaults)

Changing a constant in `timeout_defaults.py` automatically propagates to all three
layers so config, invoke, and watchdog cannot drift independently.

### Timeout Policy and Fire Reasons

`TimeoutPolicy` is the single runtime authority for all timeout dimensions. It is
constructed once per invocation from operator-supplied config and passed to
`IdleWatchdog` and `PostExitWatchdog`.

Fire conditions are evaluated in this order:

1. **SESSION_CEILING_EXCEEDED** — absolute wall-clock cap; checked first on every tick; activity cannot reset it.
2. **REPEATED_ERROR_LOOP** — repeated-error circuit breaker (consecutive-identical-error streak OR windowed-count rule); checked after the session ceiling so it fires ahead of the idle deadline for fast retry storms.
3. **STALLED_AFTER_TOOL_RESULT** — fires when a TOOL_RESULT activity was observed and no follow-up STREAM_DELTA/OUTPUT_LINE/TOOL_USE/LIFECYCLE activity arrives within `post_tool_result_progression_seconds` (default 120s). Catches the post-tool-result wedge before the 300s idle default.
4. **CHILDREN_PERSIST_TOO_LONG** — cumulative `WAITING_ON_CHILD` ceiling; never decays; uses the no-progress ceiling when corroboration shows non-progress evidence (heartbeat-only / stale-label / OS-descendant-only) and the full ceiling when corroboration shows fresh progress. This is the fire that the WAITING_ON_CHILD branch can produce; it is checked BEFORE the per-channel evidence deferral so a productive-but-silent subagent still hits the cumulative ceiling at its deadline.
5. **NO_OUTPUT_DEADLINE** — idle timeout since last agent output (+ drain window). Fires in the ACTIVE branch only (after `classify_quiet()` returns ACTIVE). Deferrable by the per-channel evidence layer (Layer 0 above) when a non-stdout channel is fresher than `agent_idle_activity_evidence_ttl_seconds`.
6. **PROCESS_EXIT_HANG** — subprocess closed stdout but did not exit within budget (post-exit, owned by PostExitWatchdog).
7. **DESCENDANT_HANG** — descendant-wait deadline elapsed post-exit (owned by PostExitWatchdog).

Suspicion (`SUSPECTED_FROZEN`) is purely informational and does NOT affect any fire condition; crossing `suspect_waiting_on_child_seconds` only emits an elevated warning event before the hard stop.

### Child Liveness Evidence (AliveBy)

When the idle deadline elapses and children appear active, the watchdog
consults a corroborator that classifies the child's liveness as one of:

| `AliveBy` value | Meaning |
|---|---|
| `fresh_progress` | Child sent a progress signal within `child_progress_ttl_seconds`. |
| `fresh_heartbeat_only` | Child sent a heartbeat within `child_heartbeat_ttl_seconds` but no recent progress. |
| `stale_label_only` | Child was registered but its evidence is stale (no fresh heartbeat or progress). |
| `os_descendant_only_stale_progress` | OS descendant scan shows active processes but Ralph has no fresh registry evidence. |

### Dual-Ceiling Design

The watchdog uses one of two ceilings for `WAITING_ON_CHILD` deferral:

- **Full ceiling** (`max_waiting_on_child_seconds`, default 1800s): applied when
  the corroborator reports `fresh_progress`, or when `max_waiting_on_child_no_progress_seconds`
  is disabled (`None`).
- **No-progress ceiling** (`max_waiting_on_child_no_progress_seconds`, default 600s):
  applied when corroboration shows `fresh_heartbeat_only`, `stale_label_only`, or
  `os_descendant_only_stale_progress` — child is alive but not making forward progress.

This prevents a stuck agent (alive OS descendants, stale progress) from waiting
the full 30 minutes before timing out, while still granting genuine progress the
full ceiling to avoid false positives.

### One Snapshot Per Tick

For each `WAITING_ON_CHILD` tick, the watchdog captures exactly one corroboration
snapshot and reuses it for: (a) effective ceiling selection, (b) suspicion
diagnostics, and (c) hard-stop diagnostics. Multiple calls to the corroborator
within a single tick are explicitly prevented to avoid snapshot divergence.

### Structured Waiting Events

While in `WAITING_ON_CHILD` deferral, structured `WaitingStatusEvent` objects are
emitted to an optional listener. Event kinds:

- `ENTERED` — transition into `WAITING_ON_CHILD` state.
- `PROGRESS` — periodic status (rate-limited to `waiting_status_interval_seconds`).
- `SUSPECTED_FROZEN` — cumulative time crossed `suspect_waiting_on_child_seconds`.
- `EXITED` — transition out of `WAITING_ON_CHILD` state.
- `HARD_STOP` — immediately before firing `CHILDREN_PERSIST_TOO_LONG`.

`HARD_STOP` events include a `diagnostic` dict with `cumulative`, `run_elapsed`,
`ceiling`, `effective_ceiling` (`standard` or `no_progress`), `evidence` string,
`alive_by`, and corroborating signal counts.

### Distinguishing Evidence Classes in Logs

From logs alone, operators can distinguish:

- `alive_by=fresh_progress` → genuine child work; full ceiling applies.
- `alive_by=fresh_heartbeat_only` → child alive but idle; no-progress ceiling applies.
- `alive_by=stale_label_only` → child registered but evidence gone stale; no-progress ceiling.
- `alive_by=os_descendant_only_stale_progress` → descendant exists but Ralph has no fresh
  registry evidence; no-progress ceiling applies. Common during network instability.

Network instability can cause `os_descendant_only_stale_progress` evidence even when the
child is doing real work (heartbeats lost). The no-progress ceiling (600s default) provides
a grace window before timing out so brief connectivity issues do not cause false positives.

## Related Documentation

- [Event Loop and Reducers](./event-loop-and-reducers.md) - How event loop integrates with reducers
- [Effect System](./effect-system.md) - How effects drive I/O
- [Workspace Trait](../agents/workspace-trait.md) - Filesystem abstraction

## Future Extensions

- Provider streaming log capture (infrastructure exists, not yet used)
- Debug artifacts directory (reserved for future use)
- Configurable log retention policies
- Log aggregation and analysis tools
