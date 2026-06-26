# Memory and Process-Lifecycle Contract

Ralph's memory and process-lifecycle invariants, the bounded-subprocess
audit that enforces them, and the known limitations operators must
understand. The audit at `ralph/testing/audit_mcp_timeout.py` and its
expansion (this document) make unbounded blocking calls structurally
hard to reintroduce in the paths that matter.

## Single teardown path

Every spawned child flows through `ProcessManager`
(`ralph/process/manager/_process_manager.py`). There is exactly one
shutdown chain, and it has three layers of defense:

1. **Explicit call**: `get_process_manager().shutdown_all(grace_period_s=...)`
   or `shutdown_all_for_label(prefix, grace_period_s=...)`. The pipeline
   emits these from its `_cleanup_pipeline` finally block
   (`ralph/pipeline/run_loop.py:_cleanup_pipeline`).
2. **Phase scope**: `process_phase_scope(phase_name)` context manager
   (`ralph/process/manager/_singleton.py`) tears down
   `phase:<phase_name>`-labeled processes on exit. Use this when a
   phase spawns short-lived children it owns.
3. **Label prefix**: per-parallel-worker scope via
   `shutdown_all_for_label("agent:<scope>:<unit_id>:", ...)`. The
   segmented prefix avoids matching sibling units.

Two safety nets wrap the explicit path:

- **atexit** (`ralph/process/manager/_singleton.py:_atexit_shutdown`):
  registered on first `get_process_manager()` call, runs
  `shutdown_all(grace_period_s=0.5)` on interpreter exit so a crash/abort
  still reaps every tracked child.
- **Signal handlers** (`ralph/interrupt/`): first SIGINT/SIGTERM
  triggers a graceful `shutdown_all`; a second signal escalates to
  `os._exit`. The first signal MUST keep the agent alive enough to
  flush its own state before the second signal lands.

### Zombie reaper

`ProcessManager` runs a daemon+joined zombie reaper
(`_start_zombie_reaper`, joined on `shutdown_all`) that periodically
reconciles active records against the OS process table. Stale PIDs are
moved to a terminal status with `cause=stale_entry_reconciled` or
`cause=zombie_reconciled` so the tracking state never drifts after an
abnormal exit.

## Bounded-resource rules

Every collection that can grow in a long-lived process must carry a
FIFO/size cap. The current inventory:

| Collection | Cap | Owner | Mechanism |
|---|---|---|---|
| ProcessManager terminal records | 256 | `ProcessManagerPolicy.terminal_history_limit` | FIFO eviction via `OrderedDict` |
| ProcessManager event listeners | 64 | `ProcessManager` (private) | FIFO eviction when the cap is hit |
| MediaManifest entries | 256 | `ralph/display/media_manifest.py` | LRU/byte-cap eviction |
| Tool catalog cache | 32 | `ralph/mcp/...` (catalog layer) | LRU eviction |
| ActivityRouter per-unit buffers | bounded | `ActivityRouter` | bounded by policy |

Every blocking I/O call MUST carry a `timeout=` keyword (or a
justified `# mcp-timeout-ok: <reason>` marker) — see
[Bounded-subprocess audit](#bounded-subprocess-audit) below. No
production code may call `subprocess.run(...)`,
`handle.communicate(...)`, `handle.wait(...)`,
`urllib.request.urlopen(...)`, `httpx.<verb>(...)`, or
`socket.create_connection(...)` without a timeout.

All background threads are `daemon=True`, gated by a stop event
(`threading.Event`), and explicitly joined on shutdown. The audit
`audit_mcp_timeout.py` enforces this contract by AST. No `__del__`
finalizers are allowed — they are non-deterministic under GC pressure
and would re-introduce the leak classes this contract prevents.

## Bounded-subprocess audit

The `ralph.testing.audit_mcp_timeout` AST-based audit enforces:

- `subprocess.run(...)` / `subprocess.call(...)` /
  `subprocess.check_call(...)` / `subprocess.check_output(...)` with
  `timeout=`
- `.communicate(...)` / `.communicate_and_cleanup(...)` with
  `timeout=` (first positional is `input`, NOT a timeout)
- `.wait(...)` with a timeout (positional or keyword)
- Network calls (`httpx.*`, `requests.*`, `urllib.request.urlopen`,
  `socket.create_connection`) with `timeout=`
- No `for line in proc.stdout:` style unbounded stream iteration (the
  reader must be interruptible)
- No `subprocess.getoutput` / `subprocess.getstatusoutput` /
  `os.system` (no timeout at all; require explicit
  `# mcp-timeout-ok` marker)

Audit roots (the directories scanned by default):

| Root | Purpose |
|---|---|
| `ralph/mcp` | MCP server thread — primary hang vector |
| `ralph/git` | Git operations (status, rebase, vendor-drift checks) |
| `ralph/process` | Subprocess lifecycle layer (`ProcessManager` and friends) |
| `ralph/executor` | Sync + async process runners (`run_process`, `run_process_async`) |
| `ralph/agents` | Subprocess agent executor (`SubprocessAgentExecutor`) |
| `ralph/pro_support` | Bounded Pro heartbeat client (network I/O) |

Adding a NEW unbounded call in any audited root fails `make verify`
on the audit step. Inline `# mcp-timeout-ok: <reason>` markers are
allowed only when the call is bounded by an enclosing
`asyncio.wait(timeout=...)` / `asyncio.wait_for(timeout=...)` / the
activity-aware idle watchdog teardown / a similarly-justified bound.
Each marker MUST name the bound.

### Executor audit detail (post-Step 1 + Step 2)

The bounded-subprocess audit was extended (Step 5) to also cover
`ralph/executor` and `ralph/agents`. Five pre-existing calls were
surfaced; all five are bounded by design:

| File | Line | Bound |
|---|---|---|
| `ralph/executor/process.py` (async path) | `await asyncio.wait({communicate_task}, timeout=timeout)` | `asyncio.wait(timeout=...)` |
| `ralph/executor/process.py` (BaseException path) | `await asyncio.wait_for(handle.wait(), timeout=0)` | `wait_for(timeout=0)` |
| `ralph/executor/process.py` (BaseException path, sync) | `handle.wait(timeout=0)` | bounded |
| `ralph/agents/subprocess_executor.py` (gather) | `await asyncio.gather(drain_output(), handle.wait())` | activity-aware idle watchdog teardown (the surrounding finally block always terminates a non-terminal handle) |
| `ralph/agents/subprocess_executor.py` (finally) | `await asyncio.wait_for(handle.wait(), timeout=0.5)` | `wait_for(timeout=0.5)` |

The one genuinely unbounded blocking call in `ralph/executor/process.py`
(the post-terminate `handle.communicate()` drain after
`terminate(grace_period_s=0)`) was bounded in Step 1 with a
`_POST_TERMINATE_DRAIN_SECONDS: Final[float] = 5.0` defense-in-depth
constant. A SIGKILL-ignoring child in uninterruptible D-state cannot
hang the caller forever — the drain raises `subprocess.TimeoutExpired`
after the bound and the executor returns the standard
`ProcessResult(returncode=TIMEOUT_EXIT_CODE)` with empty stdout/stderr
(see
`tests/test_executor_process.py::test_run_process_post_terminate_drain_is_bounded`).

## Transport teardown

`StdioTransport.close()` (`ralph/mcp/protocol/transport.py`) joins the
reader/writer daemon threads with a `_CLOSE_THREAD_JOIN_SECONDS=2.0`
bound. The reader exits promptly when the child is terminated and
stdout is closed (EOF on `for raw_line in proc.stdout`). The writer
polls `_send_queue.get(timeout=0.1)` and observes `_closed` within
~0.1s. A wedged thread that ignores these signals MUST NOT block
`close()` forever; `daemon=True` is retained so interpreter exit still
reaps them. `getattr(self, "_reader_thread", None)` guards the
un-started case so `close()` on an un-started transport is a no-op.

The regression is pinned by
`tests/test_mcp_transport.py::test_stdio_transport_close_joins_threads`
which asserts via the INJECTED `_FakeThread` double only
(`tests/test_mcp_transport_helper__fakethread.py` records `join()`).
No production private attributes are read.

## Executor teardown

`SubprocessAgentExecutor.run()` wraps the gather
(`drain_output()`, `handle.wait()`) in a try/finally that ALWAYS
terminates a non-terminal handle on exit. This is the foundation for
the `# mcp-timeout-ok: bounded by activity-aware idle watchdog
teardown` marker on the gather — a hard `wait_for` ceiling around the
gather would risk killing slow-but-healthy agents.

The regression is pinned by
`tests/test_subprocess_agent_executor_teardown.py::test_finally_block_terminates_non_terminal_handle`
(NOT subprocess_e2e-marked, so `make verify` actually runs it) and
the counter-test
`test_normal_completion_does_not_terminate_via_finally` proving the
finally block is a safety net, not a mandatory kill.

## Known limitations

- **Per-call stdio upstream spawn**: `ralph/mcp/upstream/_stdio_upstream_client.py`
  spawns a fresh subprocess per `tools/list` / `tools/call` to a stdio
  upstream MCP server. Each call is bounded (per-call `timeout=`), but
  the overhead is high — re-spawning the process on every call. A
  future optimization could reuse the upstream process across calls
  (and reuse its `ProcessManager` registration), but the current
  per-call model is a deliberate simplicity trade-off that the team
  has not yet decided to revisit.

- **Asyncio gather with no hard ceiling on healthy agents**: the
  `SubprocessAgentExecutor` gather does NOT wrap the healthy agent
  path in `wait_for`. The bounded-resource contract here is the
  activity-aware idle watchdog (which sees fresh progress signals
  from non-stdout channels) and the surrounding finally teardown. A
  pathological agent that emits neither output nor observable side
  effects is caught by the watchdog's `CHILDREN_PERSIST_TOO_LONG`
  ceiling (default 600s) via the `os_descendant_only_*` tunables, not
  by a hard wait_for.

## See also

- `ralph/process/README.md` — ProcessManager reference
- `ralph/mcp/ARCHITECTURE.md` — MCP package architecture
- `docs/agents/timeout-policy.md` — idle watchdog timeout policy
- `ralph/testing/audit_mcp_timeout.py` — the audit implementation
- `tests/test_audit_mcp_timeout.py` — audit regression tests