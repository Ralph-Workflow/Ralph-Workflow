# ProcessManager

Single source of truth for every child process Ralph spawns.

## Purpose

All child processes go through `ProcessManager`. No direct `subprocess.Popen`,
`asyncio.create_subprocess_exec`, or OS kill APIs anywhere else in the codebase.

## Public API

| Symbol | Description |
|---|---|
| `spawn(command, *, label, ...)` | Spawn a synchronous child; returns `ManagedProcess` |
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

psutil handles Linux, macOS, and Windows process tree teardown. No POSIX-only
APIs (`killpg`, `setsid`, `signal.SIGTERM`, `signal.SIGKILL`, `setpgrp`) appear
in the manager or its direct callers. The `start_new_session=True` kwarg is
allowed (it is a `Popen` parameter, not a direct POSIX call).

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

## Escalation

1. `psutil.terminate()` sent to the root process and all descendants.
2. Wait `grace_period_s`.
3. `psutil.kill()` sent to survivors.
4. Wait `kill_followup_timeout_s`.
5. If still alive → raise `ProcessTerminationError` (process is marked `KILLED`
   regardless so the record stays consistent).

## atexit safety net

`get_process_manager()` registers an `atexit` hook on first call that calls
`shutdown_all(grace_period_s=0.5)`. This is a last-resort net for crash/abort
scenarios — always prefer explicit `shutdown_all` or `process_phase_scope`.
