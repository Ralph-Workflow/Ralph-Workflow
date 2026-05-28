# ProcessManager

Single source of truth for every child process Ralph spawns.

## Purpose

All child processes go through `ProcessManager`. No direct `subprocess.Popen`,
`asyncio.create_subprocess_exec`, or OS kill APIs anywhere else in the codebase.

## Public API

| Symbol | Description |
|---|---|
| `spawn(command, *, label, ...)` | Spawn a synchronous child; returns `ManagedProcess` |
| `spawn_pty(command, *, label, ...)` | Spawn a PTY-backed child; returns `ManagedPtyProcess` |
| `spawn_async(command, *, label, ...)` | Spawn an async child; returns `ManagedAsyncProcess` |
| `register_listener(callback)` | Subscribe to `ProcessEvent` stream; returns unsubscribe callable |
| `terminate(handle, ...)` | Escalated termination for a `ManagedProcess` |
| `shutdown_all(*)` | Terminate all tracked active processes |
| `shutdown_all_for_label(prefix, *)` | Terminate active processes whose label starts with `prefix` |
| `process_phase_scope(phase_name)` | Context manager: tears down `phase:<phase>` processes on exit |
| `get_process_manager()` | Module-level singleton |
| `reset_process_manager()` | Replace singleton; use in test teardown |

## Lifecycle state machine

```
SPAWNED → RUNNING → EXITED    (process exited on its own)
                  → KILLED    (manager terminated it)
SPAWNED → FAILED              (spawn itself failed — binary not found, etc.)
```

`FAILED` is reserved for spawn-time failures only. It never means "the process
did something wrong at runtime."

## Success / failure rule

**The ProcessManager never infers success or failure from the exit code.**

Success or failure of a work unit is decided by the pipeline from:
- Artifact submission (files under `.agent/artifacts/`)
- Git workspace changes (`git status --porcelain`)

A process that exits with code `7` has `status=EXITED` and `returncode=7`.
Whether that represents success is up to the pipeline's empirical-evidence check,
not the manager.

## Cross-platform

The ProcessManager's ordinary subprocess lifecycle (`spawn`, `spawn_async`, tree teardown)
remains cross-platform through psutil on Linux, macOS, and Windows.

`spawn_pty(...)` is different: it is intentionally **POSIX-only** because a real unattended
interactive Claude session requires PTY and controlling-terminal APIs such as `openpty`,
`fork`, `setsid`, and `TIOCSCTTY`. On Windows, callers must use a headless transport instead
of the PTY-backed interactive Claude path.

## ProcessManagerPolicy

| Field | Type | Default | Description |
|---|---|---|---|
| `default_grace_period_s` | `float` | `5.0` | Max time to wait after graceful terminate |
| `kill_followup_timeout_s` | `float` | `2.0` | Max time to wait after force kill |
| `log_events` | `bool` | `True` | Enable loguru event listener |
| `terminal_history_limit` | `int` | `256` | Max terminal records to retain |
| `purge_on_init` | `bool` | `False` | Clear terminal records on ProcessManager init |

## Label conventions

| Label | Used for |
|---|---|
| `agent:<scope>:<unit_id>:root` | Scoped parallel worker agent root processes |
| `phase:<phase_name>` | Processes spawned inside a non-parallel phase |
| `phase:<phase_name>:mcp-server` | MCP server for a specific phase |
| `phase:<phase_name>:git:<op>` | Git subprocess inside a phase (when labeled) |
| `mcp-server` | Pipeline-scoped MCP server not tied to a single phase |

`shutdown_all_for_label` uses prefix matching, so `phase:review` also matches
`phase:review:mcp-server`. Agent-worker teardown should therefore target the
segment-delimited prefix `agent:<scope>:<unit_id>:` rather than a bare unit id.

## Pre-kill liveness verification

Before each termination stage, ProcessManager calls `_verify_process_liveness(pid)`
to check whether the process still exists at the OS level. This prevents:

- **TOCTOU races**: process exits between observation and kill attempt
- **Zombie false-positives**: zombie processes detected as still-alive

On **POSIX**, `os.kill(pid, 0)` probes process existence. On **Windows**,
`psutil.pid_exists()` provides the equivalent. `psutil.Process.status()` is
used for zombie detection when available.

The function returns a `LivenessResult` enum:

- `ALIVE` — process exists and is reachable
- `GONE` — process does not exist (no such process)
- `ZOMBIE` — process exists but is a zombie (defunct)
- `UNKNOWN` — liveness cannot be determined

## Escalation

1. **Pre-kill liveness check**: if the process is already gone, mark as KILLED
   with cause `already_gone` and return immediately.
2. `psutil.terminate()` sent to the root process and all descendants.
3. Wait `grace_period_s`.
4. `psutil.kill()` sent to survivors.
5. Wait `kill_followup_timeout_s`.
6. **Post-kill zombie detection**: survivors are checked for zombie status.
   If zombie → marked KILLED with cause `zombie_after_kill`.
7. If truly still alive → raise `ProcessTerminationError` with stage `force_kill`
   and process is marked `FAILED`.

## Stale-entry reconciliation

`shutdown_all()` and `shutdown_all_for_label()` scan active records before
termination. Any PID that no longer corresponds to an OS process is moved to
the terminal state via `_mark_killed()` with cause `stale_entry_reconciled`.
Zombie PIDs are reconciled with cause `zombie_reconciled`.

This ensures tracking state does not drift after abnormal exit, crash, or
interrupted cleanup.

## Idempotency

- `ManagedProcess.terminate()`, `ManagedPtyProcess.terminate()`, and
  `ManagedAsyncProcess.terminate()` are idempotent: calling terminate twice on
  the same handle is safe and produces no duplicate events.
- `shutdown_all()` and `shutdown_all_for_label()` safely handle processes
  already in a terminal state.

## Descendant tracking

`register_descendant(parent_pid, descendant_pid)` registers a descendant PID
under a tracked parent. When the parent is terminated, the descendant
registry is cleaned up. Use this for processes that spawn their own children
outside the ProcessManager's direct spawn path.

`list_termination_outcomes()` returns per-PID termination stage and outcome
records for diagnostics.

## ProcessTerminationError

`ProcessTerminationError` provides structured failure context:

- `stage` — which escalation stage failed:
  `graceful_terminate`, `force_kill`, `zombie_detected`, `access_denied`,
  or `already_gone`
- `reason` — human-readable explanation of what went wrong
- `descendant_pids` — optional list of descendant PIDs that could not be
  terminated

## purge_on_init

When `ProcessManagerPolicy(purge_on_init=True)`, terminal records are cleared
from history at `ProcessManager.__init__` time. This is useful in test
environments or when starting with a clean slate.

## atexit safety net

`get_process_manager()` registers an `atexit` hook on first call that calls
`shutdown_all(grace_period_s=0.5)`. This is a last-resort net for crash/abort
scenarios — always prefer explicit `shutdown_all` or `process_phase_scope`.

## MCP server monitoring and restart

The MCP server is a subprocess managed by ProcessManager like any other child.
Restart logic lives in `ralph.mcp.server.lifecycle.RestartAwareMcpBridge`, which:

1. Reserves one localhost port at bridge creation time and reuses it on every restart
   so `MCP_ENDPOINT_ENV` remains constant for running agents.
2. Treats the server as unhealthy when **either** the subprocess exits **or** the
   subprocess is alive but a responsiveness probe fails: `probe_mcp_http_endpoint`
   (in `ralph.mcp.protocol.startup`) sends an isolated `initialize` / `tools/list`
   handshake using a fresh session (never the agent's) and raises on timeout.
3. On an unhealthy result, calls `StandaloneMcpProcess.shutdown()` on the stale
   process, then spawns a new one via `ProcessManager.spawn()` with fresh preflight.
4. Tracks a bounded restart budget (`McpRestartPolicy.max_restarts = 1000` by default)
   and raises `McpServerError` once exhausted so the pipeline gets a crisp failure.

`ralph.process.mcp_supervisor.McpSupervisor` wraps an active attempt and polls
`check_mcp_bridge_health(bridge)` every 2 s (configurable via
`MCP_SUPERVISION_INTERVAL_MS`) in a background thread. This surfaces both
process crashes and hung-but-alive servers before they produce opaque timeouts.
The probe timeout defaults to 5 s and is configurable via `RALPH_MCP_PROBE_TIMEOUT_MS`.

ProcessManager remains the **only** process spawner and terminator; the bridge
consumes the manager's APIs and never holds raw `Popen` handles outside them.
Listeners registered via `ProcessManager.register_listener` receive events for
MCP server spawns and terminations the same as for any other child process.

When at least one restart occurs, the count is forwarded to
`PipelineSubscriber.record_mcp_restart()` and surfaced as `mcp_restarts: <n>`
in the run-end debug output. Active labeled processes from `list_active()` are
likewise rendered as `active_processes:` when non-empty.
