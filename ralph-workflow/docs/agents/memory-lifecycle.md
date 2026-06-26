# Memory and Process-Lifecycle Contract

Ralph's memory and process-lifecycle invariants, the bounded-subprocess
audit that enforces them, and the known limitations operators must
understand. The audit at `ralph/testing/audit_mcp_timeout.py` and its
expansion (this document) make unbounded blocking calls structurally
hard to reintroduce in the paths that matter.

## Performance profile

The prompt's "optimize performance" requirement is closed by the same
work that closes the memory half of the contract: the hot-path hotspots
that produce unbounded CPU, latency, or memory growth ARE the leaks this
contract prevents. The closure map below documents where each perf risk
is bounded and where the contract deliberately does NOT optimize.

| Hotspot | Risk class | Bounded by | Source-of-truth file/line |
|---|---|---|---|
| `_transcript_thread` transcript fd | Unbounded fd growth on the hot PTY-read path when readline/parse raises mid-loop | `try/finally` close in `PtyLineReader._transcript_thread` | `ralph/agents/invoke/_pty_line_reader.py` |
| Async-termination default-executor borrowing | Unbounded thread growth in teardown when many concurrent async terminates are dispatched | Dedicated bounded `ThreadPoolExecutor` owned by `ProcessManager`, released by `shutdown_all(wait=False)` | `ralph/process/manager/_process_manager.py` |
| Background threads | Non-daemon threads blocking process exit | `daemon=True` on every `threading.Thread` (enforced by `audit_resource_lifecycle.py`) | `ralph/testing/audit_resource_lifecycle.py` |
| HTTP client construction | Leaked httpx/requests clients holding connections open | `with`-context-manager usage (enforced by `audit_resource_lifecycle.py`) | `ralph/testing/audit_resource_lifecycle.py` |
| Raw `os` fd creation | Untracked fds outside the centralized process layer | Centralized under `ralph/process/` (enforced by `audit_resource_lifecycle.py`) | `ralph/testing/audit_resource_lifecycle.py` |

Why this is the entire closure map:

1. The two genuine unbounded-growth hotspots on the perf-critical paths
   (the read path and the teardown path) are the ONLY classes of
   perf-degrading leak the audit detects, and both are fixed in this
   contract. No separate CPU/latency hot-path performance work remains
   in scope — every read-path allocation that survives a long session
   has a bounded registry (terminal records cap 256, listeners cap 64,
   bounded `BoundedLinesQueue`, FIFO evictions on every collection).

2. The structural-prevention audit (`audit_resource_lifecycle.py`,
   wired into `make verify`) programmatically re-confirms that no
   other non-daemon-thread, leaked-client, or raw-fd growth exists
   today, and that any future regression in these classes fails
   `make verify`.

3. The deliberate non-reversals documented under
   [Known limitations](#known-limitations) are the remaining performance
   trade-offs the contract accepts: the per-call stdio upstream spawn
   (simplicity over latency; explicitly out of scope here) and the
   absence of `__del__` / `weakref.finalize` finalizers (rejected on
   determinism grounds). No other perf trade-off exists by design.

The contract's stance: **if a future commit grows a new unbounded
allocation on the hot path, it is caught by the audit (or the next
audit extension) BEFORE it ships, not after a long-running session
shows OOM in production**.

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
| MediaManifest entries | 256 | `ralph/mcp/multimodal/resources.py` | count cap `_DEFAULT_MAX_ENTRIES=256` + FIFO eviction (`OrderedDict.popitem(last=False)` via `_evict_oldest()`) + wt-024 M2 `retain_raw_bytes` (drops `_raw_bytes` when `byte_loader` or `cache_path` is supplied) |
| Tool catalog cache | 32 | `ralph/mcp/...` (catalog layer) | LRU eviction |
| ActivityRouter per-unit buffers | bounded | `ActivityRouter` | bounded by policy |
| `BudgetState.failures` (recovery) | none | `ralph/recovery/budget_state.py` | field DROPPED in wt-024 memory-perf AC-01 (was an unbounded `tuple[ClassifiedFailure, ...]` accumulator never read for any decision; the failures tuple retained heavyweight `ClassifiedFailure` objects across a long run — closed by dropping the field + adding `tests/integration/test_recovery_budget_memory_regression.py`) |
| `RalphAuditSinkAdapter._records` | 4096 | `ralph/mcp/artifacts/audit_adapter.py` | constructor-injected cap (`__init__(cap=_DEFAULT_AUDIT_RECORD_CAP)`) backed by `collections.deque(maxlen=cap)` FIFO eviction; `flush()` returns `None` per the `AuditSink` Protocol and CLEARS the buffer (no longer a documented no-op); `drain_records()` returns + clears |
| `_allocated_codex_homes` (Codex runtime) | 64 | `ralph/mcp/transport/codex.py` (`_DEFAULT_CODEX_HOME_CAP`) | `collections.deque(maxlen=64)` FIFO eviction of the in-memory REGISTRY bookkeeping only. **Active-home invariant (wt-024 round-2 fix):** the FIFO eviction NEVER rmtree's the evicted on-disk `CODEX_HOME` directory — it only removes the registry entry. The on-disk bound is provided by (1) `release_codex_home` invoked from the per-invocation `ResolvedInvocationRuntime.cleanup` hook (`ralph/agents/invoke/_runtime_resolvers/__init__.py:CodexRuntimeResolver`) in `invoke_agent`'s `finally` block, which pairs `release_codex_home(home)` with an unconditional `shutil.rmtree(home, ignore_errors=True)`, AND (2) `cleanup_codex_homes` (atexit net) for orphans. An earlier implementation rmtree'd on allocation-past-cap, which analysis feedback wt-024 round 2 found could delete a still-active `CODEX_HOME` directory out from under a running Codex agent when more than 64 concurrent invocations occurred; the regression is pinned by `tests/integration/test_codex_home_live_sessions.py::test_live_codex_runtimes_active_homes_survive_registry_eviction`. |

Every blocking I/O call MUST carry a `timeout=` keyword (or a
justified `# mcp-timeout-ok: <reason>` marker) — see
[Bounded-subprocess audit](#bounded-subprocess-audit) below. No
production code may call `subprocess.run(...)`,
`handle.communicate(...)`, `handle.wait(...)`,
`urllib.request.urlopen(...)`, `httpx.<verb>(...)`, or
`socket.create_connection(...)` without a timeout. An explicit
`timeout=None` (or `.wait(None)`) is treated as UNBOUNDED by the
audit: keyword presence alone is not enough because the underlying
CPython call honors the documented "no timeout" semantics when
`timeout is None`. Only a non-None literal value is accepted; a
variable that resolves to `None` at runtime is out of scope (dataflow
tracking would be required to prove it).

All background threads are `daemon=True`, gated by a stop event
(`threading.Event`), and explicitly joined on shutdown. The audit
`audit_mcp_timeout.py` enforces this contract by AST. No `__del__`
finalizers are allowed — they are non-deterministic under GC pressure
and would re-introduce the leak classes this contract prevents.

## Bounded-subprocess audit

The `ralph.testing.audit_mcp_timeout` AST-based audit enforces:

- `subprocess.run(...)` / `subprocess.call(...)` /
  `subprocess.check_call(...)` / `subprocess.check_output(...)` with
  a **non-None** `timeout=` keyword
- `.communicate(...)` / `.communicate_and_cleanup(...)` with a
  **non-None** `timeout=` keyword (first positional is `input`, NOT
  a timeout)
- `.wait(...)` with a **non-None** timeout (positional or keyword).
  `.wait()`, `.wait(None)`, and `.wait(timeout=None)` are all flagged.
- Network calls (`httpx.*`, `requests.*`, `urllib.request.urlopen`,
  `socket.create_connection`) with a **non-None** `timeout=`
- No `for line in proc.stdout:` style unbounded stream iteration (the
  reader must be interruptible)
- No `subprocess.getoutput` / `subprocess.getstatusoutput` /
  `os.system` (no timeout at all; require explicit
  `# mcp-timeout-ok` marker)

The audit is AST-based and can only flag **literal** `None` values
(`ast.Constant(value=None)` and `ast.Name(id='None')`). A variable
that resolves to `None` at runtime is out of scope (would require
dataflow tracking) and is treated as bounded by default. The
explicit-None rejection is regression-pinned by
`tests/test_audit_mcp_timeout.py::test_*_with_timeout_none_is_flagged`
(the canonical regressions for the bounded-timeout loophole).

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

After the bounded `join()`, `close()` consults `thread.is_alive()` and
emits a WARNING log if the thread is still running — visible
operator signal that a reader/writer ignored the close signals, but
`close()` itself stays non-raising (the daemon thread will be reaped
at interpreter exit). Both the join liveness and the warning path are
expressed through the widened `ThreadLike` Protocol
(`join(timeout: float | None)`, `is_alive() -> bool`) so production
`close()` stays type-safe against the injected `_FakeThread` double.

The regression is pinned by:

- `tests/test_mcp_transport.py::test_stdio_transport_close_joins_threads`
  — asserts via the INJECTED `_FakeThread` double only
  (`tests/test_mcp_transport_helper__fakethread.py` records `join()`)
  that BOTH threads were joined with a non-None positive timeout. No
  production private attributes are read.
- `tests/test_mcp_transport.py::test_stdio_transport_close_warns_when_thread_still_alive`
  — asserts the warning path: when the injected thread stays alive
  past the bounded join, `close()` MUST log a WARNING naming the
  thread attribute and MUST NOT raise. Uses `_FakeThread(alive_after_join=True)`
  and a loguru string sink to capture the warning text
  deterministically.

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

## Resource-lifecycle audit

`ralph/testing/audit_resource_lifecycle.py` is the AST-based audit that
keeps the contract structurally hard to regress. It enforces four
classes of leak in production code:

| Contract | What it flags | Why it matters |
|---|---|---|
| **Daemon-Thread rule** | `threading.Thread(...)` and `Thread(...)` (resolved through import aliases) WITHOUT `daemon=True` | Non-daemon threads can block process exit on the `concurrent.futures` `_python_exit` atexit join |
| **With-managed HTTP client** | `httpx.Client(...)`, `httpx.AsyncClient(...)`, `requests.Session(...)` constructed OUTSIDE a `with` statement (bare assignment) | Bare construction leaks the underlying HTTP connection pool and is not closed at interpreter exit |
| **Centralized raw-fd creation** | `os.open(...)`, `os.openpty(...)`, `os.pipe(...)` OUTSIDE `ralph/process/` | Raw fd creation bypasses the centralized process lifecycle; the zombie reaper / terminal records don't see the fd and it leaks across restarts |
| **Resource accumulators** (wt-024 memory-perf AC-04) | Mutable collection literals (`[]`, `{}`, `set()`) and constructor calls (`list()` / `dict()` / `set()` / `deque()` / `collections.deque()` WITHOUT `maxlen=` / `OrderedDict()` / `collections.OrderedDict()` / `defaultdict()` / `collections.defaultdict()`) assigned to (a) module-level names OR (b) instance attributes (`self.X`) inside `__init__` bodies | Unbounded accumulators retain heavyweight objects (exceptions, tracebacks, large payloads) across a long unattended run — the exact leak class that produced `BudgetState.failures` and `RalphAuditSinkAdapter._records`. `OrderedDict` and `defaultdict` are flagged because they have NO `maxlen` kwarg (unlike `deque`); the FIFO escape hatch is a manual `popitem(last=False)` / `len(...) > cap` eviction policy in the code itself, and the `# bounded-accumulator-ok: <cap>` marker is the only audit-recognized escape (it MUST name the cap constant). |

The audit has TWO inline escape hatch markers on the line that
suppresses the violation:

- `# resource-lifecycle-ok: <reason>` — applies to contracts 1-3
  (daemon-Thread, HTTP client, raw-fd);
- `# bounded-accumulator-ok: <reason>` — applies to contract 4
  (resource accumulators).

Both markers are part of a single marker SET in
`audit_resource_lifecycle.py` so they coexist without disrupting
each other (a future contract can opt in by adding to the set). The
markers are the only allowlist mechanism — keep them rare and
justified (name the cap / drain).

Exclusions for the accumulator contract (intentional, documented
to avoid false positives):

- `__all__` (Python re-export convention; static list of exported
  symbol names never mutated after class load).
- Single-element list literals `[X]` (Python's mutable-closure idiom
  for capturing a counter / flag / None sentinel — the list itself is
  NOT an accumulator; `lst[0] = ...` mutates in place).
- Dict / set literals whose keys / elements are all static (strings,
  names, attribute accesses) — static dispatch / handler / config
  tables populated once at construction.
- Local variables inside non-`__init__` functions (higher false-
  positive rate; the `BudgetState.failures` leak class was closed by
  dropping the field + the tracemalloc test, not by this AST contract).
- Dataclass field defaults (`field(default_factory=...)`).

Default audit roots (the directories scanned by `make verify`):

| Root | Why it's covered |
|---|---|
| `ralph/mcp` | HTTP client + daemon threads (the SSE/HTTP request surface) |
| `ralph/agents` | Subprocess agent executor + daemon threads |
| `ralph/executor` | Sync + async process runners |
| `ralph/process` | Centralized process lifecycle (the raw-fd allowlist root) |
| `ralph/pipeline` | Run loop + interrupt threads |
| `ralph/runtime` | Runtime helper modules |
| `ralph/pro_support` | Pro heartbeat client (daemon thread + HTTP client) |
| `ralph/recovery` | Recovery control flow |
| `ralph/display` | Per-unit display accumulators (`ParallelDisplay._active_block` / `_last_worker_states` / `_overflow_logs` / ...) drained by `ParallelDisplay.drop_unit` / `ActivityRouter.drop_unit` (the parallel coordinator finally block is the active drain). `_last_budget_progress` is phase-bounded (replaced wholesale each snapshot), not per-unit. |
| `ralph/prompts` | Template registry caches (`_cache` / `_templates`) bounded by the immutable packaged-template file set and the workspace `template_dirs` lazily discovered by `_discover_template`. `register_template` has zero production callers so the bound is the file set, not a programmatic registry. |

Intentional exclusions (out of scope, documented to avoid false
positives):

- `ThreadPoolExecutor` — has its own `.shutdown()` lifecycle owned by
  the caller.
- Bare `open()` — governed by `audit_di_seam` (composition-root
  env/open reads), not this audit.
- `loop.run_in_executor(None, ...)` in `ralph/interrupt/asyncio_bridge.py`
  — bounded shutdown block owned by the asyncio bridge (different
  lifecycle), not a thread leak.

The audit is wired into `make verify` as the LAST `_VERIFY_STEPS`
entry (step 17). It is NOT a budget-tracked step, so adding it does
NOT increase the 60-second combined test budget; it does NOT trip
the `audit_mcp_timeout`-containment import-time invariant. Adding a
NEW violation in any audited root fails `make verify` on this step.

## File-handle ownership

Production code MUST use `with` or `try/finally` for file handles.
A file handle opened in the body of a function MUST be closed on
every exit path — normal return, raised exception, swallowed
exception, and any re-raise path. The canonical example of the
exception-safety failure this rule prevents:

```python
# WRONG — leaks the fd on any readline/parse raise
file_obj = path.open(...)
while not stop.is_set():
    line = file_obj.readline()      # raises here → fd leaks
    process(line)
if file_obj is not None:
    file_obj.close()                # never reached on the raise path
```

```python
# RIGHT — try/finally guarantees close on every exit path
file_obj = path.open(...)
try:
    while not stop.is_set():
        line = file_obj.readline()
        process(line)
finally:
    if file_obj is not None:
        file_obj.close()
        file_obj = None              # belt-and-suspenders; the
                                     # post-loop close (if any) is
                                     # a no-op on the closed handle
```

This is exactly the fix applied to `PtyLineReader._transcript_thread`
in this contract — the loop body is wrapped in `try/finally` so a
mid-loop raise (e.g. `transcript_lines_from_event` parse error)
closes the handle before propagating. The fix is regression-pinned by
`tests/agents/invoke/test_pty_line_reader_transcript_handle.py`.

## How to add a new resource safely

A short checklist for any new long-lived resource (process, thread,
HTTP client, file handle, accumulator):

1. **Processes**: flow through `ProcessManager`. Use the factory
   methods (`pm.spawn`, `pm.spawn_pty`, `pm.spawn_async`); never call
   `subprocess.Popen` directly. Register in `pm` so the atexit /
   signal-handler / label-prefix teardown path can find and reap it.
2. **File handles**: `with` for one-shot reads, `try/finally` for
   hot-loop / repeated reads. The `_transcript_thread` pattern is the
   canonical reference.
3. **HTTP clients**: `with httpx.Client(...) as client:` for
   request-scoped clients. For long-lived singletons, manage via an
   explicit close in a `finally` block AND add a
   `# resource-lifecycle-ok: <reason>` marker so the audit's
   production-tree scan stays green.
4. **Background threads**: `daemon=True` + a `threading.Event` stop
   signal + a bounded join on shutdown. Never rely on GC to reap a
   non-daemon thread (the contract forbids `__del__` / `weakref.finalize`
   finalizers for exactly this reason).
5. **Accumulators** (lists, deques, dicts, `OrderedDict` / `defaultdict`,
   bytes buffers): add a FIFO / size cap aligned with the production
   default. Mirrors the `ProcessManager.terminal_history_limit = 256` /
   `BoundedLinesQueue(maxlen=256)` pattern. For long-lived mutable
   accumulators (module-level OR `self.X` in `__init__`), the audit
   flags the assignment unless you use `deque(maxlen=...)` /
   `OrderedDict` + count cap / a justified
   `# bounded-accumulator-ok: <reason>` marker naming the cap or drain.
   `OrderedDict` and `defaultdict` have NO `maxlen` kwarg, so the marker
   is the only audit-recognized escape for legitimately manually-capped
   sites (the marker MUST name the source-verified cap constant, e.g.
   `ProcessManagerPolicy.terminal_history_limit`,
   `_MAX_CACHE_ENTRIES=32`,
   `_MAX_SUBAGENT_OUTPUT_CAPTURES=128`,
   `_MAX_EVICTED_TOMBSTONES`,
   `_DECISION_LOG_MAX=16`, etc.).

If a new resource class is added, extend the
`audit_resource_lifecycle.py` audit to cover it AND add an inline
marker style (`# resource-lifecycle-ok: <reason>` for contracts 1-3
or `# bounded-accumulator-ok: <reason>` for contract 4). The audit
exists to make a future leak fail `make verify` BEFORE it ships, not
after a long-running session shows OOM in production.

## See also

- `ralph/process/README.md` — ProcessManager reference
- `ralph/mcp/ARCHITECTURE.md` — MCP package architecture
- `docs/agents/timeout-policy.md` — idle watchdog timeout policy
- `ralph/testing/audit_mcp_timeout.py` — the audit implementation
- `tests/test_audit_mcp_timeout.py` — audit regression tests