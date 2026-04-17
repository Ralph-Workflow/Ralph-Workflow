# Parallel Fan-Out Architecture

This document describes Ralph's parallel development fan-out system: how a single development phase is executed across N parallel workers using structured concurrency, and how results are merged back into the main branch.

For the end-to-end pipeline lifecycle (Planning -> Development -> result verification -> Commit -> Review/Fix loops), see `pipeline-lifecycle.md`. For the event loop and reducer architecture that drives the pipeline, see `event-loop-and-reducers.md`.

## Data Flow

The parallel fan-out executes a wave-based DAG across N workers, where each wave respects dependency ordering and a configurable concurrency cap.

```
Planning phase
    |
    v
PipelineState.work_units  (tuple[WorkUnit, ...], frozen after planning)
    |
    v
FanOutDevelopmentEffect(work_units, max_workers)
    |
    v
coordinator.run_fan_out()  -- asyncio.TaskGroup --
    |
    +---> scheduler.schedule_next_wave()  --> N SubprocessAgentExecutor.run()
    |                                              |
    |                                              v
    |                                         worker subprocess
    |                                         (MCP agent in worktree)
    |                                              |
    |                                              v
    +--- WorkerStartedEvent(unit_id) <----------+
    |
    +--- WorkerCompletedEvent(unit_id, exit_code) --+
    |
    +--- WorkerFailedEvent(unit_id, exit_code, error) --+
    |
    v
ALL_WORKERS_COMPLETE  (or partial failure events)
    |
    v
MergeIntegrationEffect(worker_states, base_branch)
    |
    v
merge_integrator.integrate()  -- sequential git merge per worker branch --
    |
    v
WorkersMergeConflictEvent(conflicting_unit_ids)  --> PHASE_FAILED
    OR
ALL_WORKERS_COMPLETE  --> phase advances
```

### Wave Scheduling

`scheduler.schedule_next_wave()` selects the next wave of work units:

- Units are ready when all their `dependencies` have completed
- Ready units are sorted by `unit_id` for deterministic ordering
- Only `max_workers` units run concurrently (cap enforced by the coordinator)

## Ports and Adapters

### AgentExecutor Protocol

```python
# ralph-python/ralph/agents/executor.py
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
# ralph-python/ralph/agents/subprocess_executor.py
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
# ralph-python/ralph/testing/fake_agent_executor.py
class FakeAgentExecutor:
    def __init__(self, runs: dict[str, FakeRun]) -> None: ...
    async def run(self, unit, *, on_output, on_status) -> WorkerResult: ...
```

Returns seeded `WorkerResult` values without spawning processes. Tests use this to assert on event sequences and call counts.

### McpServerFactory Protocol

```python
# ralph-python/ralph/mcp/server/factory.py
@runtime_checkable
class McpServerFactory(Protocol):
    def build(self, session: object) -> McpServerHandle: ...
```

`McpServerFactory` is a protocol so the subprocess executor can be configured with a mock factory in tests. The production implementation is `McpServerFactoryImpl`.

### WorkspaceScope.for_worktree()

```python
# ralph-python/ralph/workspace/scope.py
@classmethod
def for_worktree(cls, worktree_path: Path, allowed_directories: tuple[str, ...]) -> WorkspaceScope:
    allowed_roots = tuple(worktree_path / ad for ad in allowed_directories)
    return cls(root=worktree_path, allowed_roots=allowed_roots)
```

Each worker subprocess is assigned its own `WorkspaceScope` pointing at the branch-specific worktree. This constrains filesystem access to the correct worktree and prevents cross-contamination.

### GitExecutor Serialization Gate

```python
# ralph-python/ralph/git/executor.py
class GitExecutor:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1)

    async def arun(self, op: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, op)
        return await future
```

GitPython is not thread-safe when multiple threads share a `.git/` directory. `GitExecutor` serializes all git operations through a single-threaded executor, ensuring that merge integration and any concurrent git operations from other pipeline stages do not corrupt the repository state.

## Concurrency Model

### Top-Level Event Loop

The pipeline event loop in `runner.run()` is synchronous. When it encounters a `FanOutDevelopmentEffect`, it delegates to `_execute_fan_out_sync()`:

```python
# ralph-python/ralph/pipeline/runner.py
def _execute_fan_out_sync(*, effect, state, display, policy_bundle, workspace_scope):
    import asyncio
    async def _run() -> PipelineState:
        # ... fan-out and merge logic ...
    return asyncio.run(_run())
```

### asyncio.TaskGroup

Within `_run()`, `coordinator.run_fan_out()` uses `asyncio.TaskGroup` for structured concurrency:

```python
# ralph-python/ralph/pipeline/parallel/coordinator.py
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

### Worker Subprocess

Each `_run_worker()` task spawns one subprocess via `asyncio.create_subprocess_exec`:

```python
# ralph-python/ralph/agents/subprocess_executor.py
proc = await asyncio.create_subprocess_exec(
    *command,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
    start_new_session=True,
)
```

The subprocess runs the MCP agent in a branch-specific worktree. Output is drained asynchronously and forwarded to the display via callbacks.

### Rich Live Display Thread

`ParallelDisplay` owns a `RenderThread` (a `threading.Thread`) that drives `rich.Live`:

```python
# ralph-python/ralph/display/parallel_display.py
class ParallelDisplay:
    def start(self) -> None:
        live = Live(console=self._console, auto_refresh=False)
        self._render_thread = RenderThread(q=self._queue, renderable_fn=..., live=live)
        self._render_thread.start()
```

The render thread is NOT a coroutine. It consumes from `queue.Queue`:

```python
# ralph-python/ralph/display/render_thread.py
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
# ralph-python/ralph/agents/subprocess_executor.py
except asyncio.CancelledError:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    raise
```

This ensures the entire process tree of the worker is killed, not just the immediate subprocess.

## State Ownership

### PipelineState is Immutable

`PipelineState` is a Pydantic model with `model_config = ConfigDict(frozen=True)`:

```python
# ralph-python/ralph/pipeline/state.py
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

Workers do not share memory. Each worker is an independent subprocess with its own worktree. The only shared artifact is `PipelineState.worker_states`, which is owned exclusively by the reducer.

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

## Invariants

### max_parallel_workers Cap

The coordinator scheduler enforces the cap before every wave:

```python
# ralph-python/ralph/pipeline/parallel/scheduler.py
def schedule_next_wave(completed, all_units, currently_running, max_workers):
    available_slots = max_workers - len(currently_running)
    if available_slots <= 0:
        return []
```

The `max_workers` value comes from `parallel_execution.max_parallel_workers` in policy, which is read from `FanOutDevelopmentEffect.max_workers` and ultimately from `_determine_effect_from_policy()` in `runner.py`.

### Work Units Immutable After Planning

`work_units` is set once during planning and stored in `PipelineState`. The `FanOutDevelopmentEffect` carries this frozen tuple:

```python
# ralph-python/ralph/pipeline/effects.py
@dataclass(frozen=True)
class FanOutDevelopmentEffect:
    work_units: tuple[WorkUnit, ...]
    max_workers: int
```

Workers read from the worktree, never from each other's outputs. No work unit may be added after the first wave starts.

### Single Writer for Checkpoint

Checkpoint writes are serialized through the main event loop. The `_execute_fan_out_sync()` function calls `ckpt.save(current)` after all fan-out events are reduced and again after merge integration:

```python
# ralph-python/ralph/pipeline/runner.py
async def _run() -> PipelineState:
    # ... fan-out ...
    for ev in fan_out_events:
        current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
    ckpt.save(current)  # checkpoint after fan-out

    # ... merge ...
    for ev in merge_result.events:
        current, _ = reducer_reduce(current, ev, policy_bundle.pipeline)
    ckpt.save(current)  # checkpoint after merge
    return current
```

When SIGINT arrives, `run()` catches `KeyboardInterrupt` and saves an immediate checkpoint before exiting:

```python
# ralph-python/ralph/pipeline/runner.py
except KeyboardInterrupt:
    interrupted_state = state.copy_with(interrupted_by_user=True)
    ckpt.save(interrupted_state)
    return 130
```

### No Cross-Worker Artifact Sharing

Each worker operates on its own branch worktree (`ralph/unit-{unit_id}`). There is no shared filesystem path between workers. Merge integration happens only after all workers complete, via `GitExecutor` serialization.

## Hard-Kill Flow

### Signal Handler Registration

The main `run()` function does not register signal handlers itself. SIGINT handling is layered:

1. User sends SIGINT (Ctrl+C)
2. Python's default SIGINT handler raises `KeyboardInterrupt` in the main thread
3. The event loop catches `KeyboardInterrupt` and saves checkpoint

### TaskGroup Cancellation

When `KeyboardInterrupt` fires during `_execute_fan_out_sync()`, Python cancels the `TaskGroup` context, which cancels all running `_run_worker` tasks via `asyncio.CancelledError`.

### Process Group Termination

Each `_run_worker()` task catches `asyncio.CancelledError` and kills its subprocess tree:

```python
# ralph-python/ralph/agents/subprocess_executor.py
except asyncio.CancelledError:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    raise
```

`os.getpgid(pid)` is not used because the process may already be gone. `os.killpg(proc.pid, signal.SIGKILL)` is called directly with the stored `proc.pid`.

### Second SIGINT

If a second SIGINT arrives before the first has been fully handled, Python's default `signal.SIGINT` behavior applies: `sys.exit(130)` is called. This is the same as the shell convention for `SIGINT` exit codes.

### Checkpoint Before Kill

The checkpoint is saved when the interrupt is caught, before any process killing occurs. This ensures that even a hard kill leaves a resumable checkpoint.

## Merge Integration

### Option B: Sequential Branch Merging

Ralph uses Option B merge strategy: all worker branches are merged into `main` sequentially, in sorted `unit_id` order.

```python
# ralph-python/ralph/pipeline/parallel/merge_integrator.py
async def integrate(base_branch, worker_states, git_executor, repo_root):
    succeeded_ids = sorted(
        unit_id for unit_id, ws in worker_states.items()
        if ws.status == WorkerStatus.SUCCEEDED
    )

    for unit_id in succeeded_ids:
        branch_name = f"ralph/unit-{unit_id}"
        result = await git_executor.arun(lambda: subprocess.run(
            ["git", "merge", "--no-ff", branch_name],
            cwd=repo_root, capture_output=True, check=False,
        ))
        if result.returncode != 0:
            await git_executor.arun(lambda: subprocess.run(
                ["git", "merge", "--abort"], cwd=repo_root, capture_output=True,
            ))
            conflicting_unit_ids.append(unit_id)
```

### Conflict Handling

If any merge returns a non-zero exit code:

1. `git merge --abort` is issued to roll back to pre-merge state
2. The `unit_id` is recorded in `conflicting_unit_ids`
3. After all merge attempts, `WorkersMergeConflictEvent(conflicting_unit_ids)` is returned

### Failure Event → Phase Transition

The reducer handles `WorkersMergeConflictEvent`:

```
WorkersMergeConflictEvent --> PipelineState.phase = PHASE_FAILED
```

On any merge conflict, the phase enters `PHASE_FAILED` and the pipeline exits with a non-zero code.

### Worktree Preservation on Failure

Worktrees are preserved on any failure because they are the git branches themselves (`ralph/unit-{unit_id}`). They are not deleted after merge. A developer can inspect them directly to resolve conflicts.

### Successful Merge Outcome

On full success (all workers merged without conflict):

```
MergeResult(success=True, events=[PipelineEvent.ALL_WORKERS_COMPLETE])
```

`ALL_WORKERS_COMPLETE` is reduced to advance the phase, continuing the pipeline to the next phase (typically review).

## See Also

- `event-loop-and-reducers.md` — event loop driver, reducer architecture, effect handler layering
- `pipeline-lifecycle.md` — end-to-end pipeline lifecycle
- `checkpoint-and-resume.md` — checkpoint semantics and resume flow
