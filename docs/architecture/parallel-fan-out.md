# Parallel Fan-Out Architecture

Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.
That simple core composes into a stronger workflow system for serious repo work, and the default workflow is already strong enough to start with before you customize anything.


This document describes how parallel plan execution is delegated to the executing AI agent in the bundled default. The Ralph-managed fan-out machinery documented below is **dormant** in this build: it is retained for future use and re-arming but is not the path that runs in the bundled default. See `## Dormant fan-out` below for the trigger and the re-arming mechanism.

The rest of this document (Data Flow, Ports and Adapters, Concurrency Model, State Ownership, Invariants) is preserved verbatim as a **reference-only** description of the dormant machinery. Sections below the divider are marked `(dormant - reference only)` and describe the legacy `ralph_fan_out` dispatch mode; the bundled default is `agent_subagents` and falls through to a single `InvokeAgentEffect` so the executing agent can dispatch its own sub-agents per the plan's `work_units` / `parallel_plan`.

## Dormant fan-out

The bundled default sets `parallelism.dispatch_mode = 'agent_subagents'` on the development phase (see `ralph/policy/defaults/pipeline.toml`). The effect router (`ralph/pipeline/effect_router.py`) reads that flag and falls through to `InvokeAgentEffect` while logging a WARNING. The WARNING string is:

> Ralph-managed fan-out is dormant in this build; the executing AI agent is expected to dispatch its own sub-agents per the plan. The declared work_units are informational; the agent will read them as parallelization intent.

The dormant marker is enforced by an audit at `ralph/testing/audit_parallelization_dormant.py` that runs under `make verify` and checks for the new wording in `planning.jinja`, the format doc, the effect-router WARNING, the bundled `pipeline.toml`, and the new ninth rubric dimension in `planning_analysis.jinja`.

To re-enable Ralph-managed fan-out on a phase, set:

```toml
[blocks.development.phase.parallelization]
mode = "same_workspace"
dispatch_mode = "ralph_fan_out"   # the legacy path; the bundled default is "agent_subagents"
max_parallel_workers = 8
```

The fan-out machinery below is unchanged and re-armable: `FanOutEffect` (in `ralph/pipeline/effects.py`), `ralph/pipeline/fan_out.py`, and `ralph/pipeline/parallel/` are kept intact and are re-invoked the moment `dispatch_mode` flips back to `ralph_fan_out`.

---

(dormant - reference only) The rest of this document describes the legacy Ralph-managed fan-out path. It is preserved here as a reference for future re-arming and for historical context; the bundled default does not invoke it.

## (dormant - reference only) Data Flow

For the end-to-end pipeline lifecycle, see `pipeline-lifecycle.md`. For the event loop and reducer architecture, see `event-loop-and-reducers.md`.

The parallel fan-out executes a wave-based DAG across N workers. Each wave respects dependency ordering and a configurable concurrency cap. Workers run against the shared checkout, isolated by path restrictions and per-worker artifact namespaces only — same-workspace v1 uses a single shared checkout with state aggregation as the only post-development coordination.

```
Planning phase
    |
    v
PipelineState.work_units  (tuple[WorkUnit, ...], frozen after planning)
    |
    v
validate_for_same_workspace()  -- pre-flight safety check --
    |
    v
FanOutDevelopmentEffect(work_units, max_workers)
    |
    v
coordinator.run_fan_out()  -- asyncio.TaskGroup --
    |
    +---> scheduler.schedule_next_wave()  --> N worker executors
    |                                              |
    |                                              v
    |                                         worker (in-process or subprocess)
    |                                         (MCP agent on shared checkout)
    |                                              |
    |                                              v
    +--- WorkerStartedEvent(unit_id) <-----------+
    |
    +--- WorkerCompletedEvent(unit_id, exit_code) --+
    |
    +--- WorkerFailedEvent(unit_id, exit_code, error) --+
    |
    v
ALL_WORKERS_COMPLETE  (or partial failure events)
    |
    v
phase advances  (no merge step)
```

### Same-Workspace Pre-Flight Validation

Before fan-out starts, `validate_for_same_workspace()` rejects any plan that would allow workers to corrupt each other's state:

- Every unit must declare at least one `allowed_directory`
- No unit may declare a reserved path (`.agent`, `.git`, `.worktrees`, `.`)
- No two units may have overlapping edit areas (segment-aware prefix check)

### Wave Scheduling

`scheduler.schedule_next_wave()` selects the next wave of work units:

- Units are ready when all their `dependencies` have completed
- Ready units are sorted by `unit_id` for deterministic ordering
- Only `max_workers` units run concurrently (cap enforced by the coordinator)

## (dormant - reference only) Ports and Adapters

### AgentExecutor Protocol

```python
# ralph-workflow/ralph/agents/executor.py
@runtime_checkable
class AgentExecutor(Protocol):
    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult: ...
```

`AgentExecutor` is a port because testability requires a fake that does not spawn real subprocesses. The `runtime_checkable` decorator enables `isinstance` checks in tests.

### SubprocessAgentExecutor (Production Adapter)

```python
# ralph-workflow/ralph/agents/subprocess_executor.py
class SubprocessAgentExecutor:
    async def run(self, unit, *, on_output, on_status, command) -> WorkerResult:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # <-- creates own process group
        )
```

`start_new_session=True` is the critical flag: it places the child in a new process group so that `os.killpg(pid, signal.SIGKILL)` terminates the entire subtree (agent + any child tools).

### FakeAgentExecutor (Test Adapter)

```python
# ralph-workflow/ralph/testing/fake_agent_executor.py
class FakeAgentExecutor:
    def __init__(self, runs: dict[str, FakeRun]) -> None: ...
    async def run(self, unit, *, on_output, on_status) -> WorkerResult: ...
```

Returns seeded `WorkerResult` values without spawning processes. Tests use this to assert on event sequences and call counts.

### McpServerFactory Protocol

```python
# ralph-workflow/ralph/mcp/server/factory.py
@runtime_checkable
class McpServerFactory(Protocol):
    def build(self, session: object) -> McpServerHandle: ...
```

`McpServerFactory` is a protocol so the in-process executor can be configured with a mock factory in tests. The production implementation is `DynamicBindingMcpServerFactory`.

### WorkspaceScope.for_same_workspace_worker()

```python
# ralph-workflow/ralph/workspace/scope.py
@classmethod
def for_same_workspace_worker(
    cls,
    repo_root: Path,
    allowed_directories: tuple[str, ...],
    worker_namespace: Path,
) -> WorkspaceScope:
    ...
```

Each worker is assigned a `WorkspaceScope` that keeps `root=repo_root` (the shared checkout) while restricting `allowed_roots` to the declared directories plus the per-worker namespace path. This constrains filesystem access without creating a new checkout.

## (dormant - reference only) Concurrency Model

### Top-Level Event Loop

The pipeline event loop in `runner.run()` is synchronous. When it encounters a `FanOutDevelopmentEffect`, it delegates to `_execute_fan_out_sync()`:

```python
# ralph-workflow/ralph/pipeline/runner.py
def _execute_fan_out_sync(*, effect, state, display, policy_bundle, workspace_scope):
    import asyncio
    return asyncio.run(_run_fan_out_async(...))
```

### asyncio.TaskGroup

Within `_run_fan_out_async()`, `coordinator.run_fan_out()` uses `asyncio.TaskGroup` for structured concurrency:

```python
# ralph-workflow/ralph/pipeline/parallel/coordinator.py
async with asyncio.TaskGroup() as task_group:
    while pending or running:
        ready = schedule_next_wave(completed, effect.work_units, set(running), effect.max_workers)
        for unit in ready:
            task_group.create_task(
                _run_worker(unit, executor, display, completion_queue),
                name=unit.unit_id,
            )
```

`TaskGroup` guarantees:
- All tasks complete or all are cancelled (no orphaned tasks)
- Exceptions from any task are captured in the `ExceptionGroup` delivered to the `except*` clause

### Rich Live Display Thread

`ParallelDisplay` owns a `RenderThread` (a `threading.Thread`) that drives `rich.Live`:

```python
# ralph-workflow/ralph/display/parallel_display.py
class ParallelDisplay:
    def start(self) -> None:
        live = Live(console=self._console, auto_refresh=False)
        self._render_thread = RenderThread(q=self._queue, renderable_fn=..., live=live)
        self._render_thread.start()
```

The render thread is NOT a coroutine. It consumes from `queue.Queue`:

```python
# ralph-workflow/ralph/display/render_thread.py
class RenderThread(threading.Thread):
    def run(self) -> None:
        while not self._stop_event.is_set():
            while True:
                try:
                    event = self._queue.get_nowait()
                    self._apply(event)
                except queue.Empty:
                    break
            self._live.update(self._renderable_fn(self._state))
            self._stop_event.wait(1 / self._refresh_hz)
```

Worker tasks call `display.emit()` and `display.set_status()` from async context; these methods put `UpdateEvent` objects into the queue. The render thread owns the `rich.Live` instance and the event loop never touches it directly.

### Process Group Kill on Cancellation

When a worker task is cancelled (SIGINT propagates to the TaskGroup), the cancellation is propagated to the subprocess via `os.killpg`:

```python
# ralph-workflow/ralph/agents/subprocess_executor.py
except asyncio.CancelledError:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    raise
```

This ensures the entire process tree of the worker is killed, not just the immediate subprocess.

## (dormant - reference only) State Ownership

### PipelineState is Immutable

`PipelineState` is a Pydantic model with `model_config = ConfigDict(frozen=True)`:

```python
# ralph-workflow/ralph/pipeline/state.py
class PipelineState(BaseModel):
    model_config = ConfigDict(frozen=True)
    # ... fields
```

Reducer transitions produce new state via `.copy_with()`:

```python
current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
ckpt.save(current)
```

No handler or worker ever mutates `PipelineState` directly.

### Worker State

Worker results flow through the reducer:

```
WorkerStartedEvent --> reducer --> PipelineState.worker_states[unit_id].status = RUNNING
WorkerCompletedEvent --> reducer --> PipelineState.worker_states[unit_id].status = SUCCEEDED
WorkerFailedEvent --> reducer --> PipelineState.worker_states[unit_id].status = FAILED
```

Workers do not share memory. The only shared artifact is `PipelineState.worker_states`, which is owned exclusively by the reducer.

### Worker Success Determination

In same-workspace mode, worker success is determined **exclusively** by worker-local artifact evidence under `.agent/workers/<unit_id>/artifacts/`. The coordinator checks `list_artifacts(artifact_dir)` after execution:

- If artifacts are present → worker succeeded
- If no artifacts found → worker fails with a descriptive error, regardless of process exit code

Repository-wide git status is **never** used as a fallback success signal.

### Event Flow

```
Reducer --> PipelineState (written) --> Checkpoint saved --> Display updated
    ^
    |
Handler (effect execution)
    ^
    |
EffectHandler (effect --> I/O) --> PipelineEvent --> Reducer
```

Handlers never write to state. Display updates happen after reducer transitions are complete.

## (dormant - reference only) Invariants

### max_parallel_workers Cap

The coordinator scheduler enforces the cap before every wave:

```python
# ralph-workflow/ralph/pipeline/parallel/scheduler.py
def schedule_next_wave(completed, all_units, currently_running, max_workers):
    available_slots = max_workers - len(currently_running)
    if available_slots <= 0:
        return []
```

### Work Units Immutable After Planning

`work_units` is set once during planning and stored in `PipelineState`. The `FanOutDevelopmentEffect` carries this frozen tuple:

```python
# ralph-workflow/ralph/pipeline/effects.py
@dataclass(frozen=True)
class FanOutDevelopmentEffect:
    work_units: tuple[WorkUnit, ...]
    max_workers: int
```

No work unit may be added after the first wave starts.

### Single Writer for Checkpoint

Checkpoint writes are serialized through the main event loop. The `_run_fan_out_async()` function calls `ckpt.save(current)` after all fan-out events are reduced:

```python
# ralph-workflow/ralph/pipeline/runner.py
for ev in fan_out_events:
    current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
ckpt.save(current)  # checkpoint after fan-out
return current
```

### No Cross-Worker Edit Area Overlap

Each worker declares non-overlapping `allowed_directories`. The pre-flight `validate_for_same_workspace()` enforces this before any worker starts. Workers never read from each other's edit areas.

## (dormant - reference only) Hard-Kill Flow

### Signal Handler Registration

Signal handling is layered:

1. User sends SIGINT (Ctrl+C)
2. Python's default SIGINT handler raises `KeyboardInterrupt` in the main thread
3. The event loop catches `KeyboardInterrupt` and saves checkpoint

### TaskGroup Cancellation

When `KeyboardInterrupt` fires during `_execute_fan_out_sync()`, Python cancels the `TaskGroup` context, which cancels all running `_run_worker` tasks via `asyncio.CancelledError`.

### Process Group Termination

Each `_run_worker()` task catches `asyncio.CancelledError` and kills its subprocess tree:

```python
# ralph-workflow/ralph/agents/subprocess_executor.py
except asyncio.CancelledError:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    raise
```

### Checkpoint Before Kill

The checkpoint is saved when the interrupt is caught, before any process killing occurs. This ensures that even a hard kill leaves a resumable checkpoint.

## See Also

- `event-loop-and-reducers.md` — event loop driver, reducer architecture, effect handler layering
- `pipeline-lifecycle.md` — end-to-end pipeline lifecycle
- `checkpoint-and-resume.md` — checkpoint semantics and resume flow
