from __future__ import annotations

import importlib
import importlib.util
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import loguru
import pytest
from loguru import logger

from ralph.agents.executor import WorkerResult
from ralph.display.activity_router import ActivityRouter
from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

if TYPE_CHECKING:
    from pathlib import Path

RunFanOut = Callable[..., Awaitable[list[Event]]]
TOTAL_CAP_UNITS = 5
MAX_CAP_WORKERS = 2


def _load_run_fan_out() -> RunFanOut:
    spec = importlib.util.find_spec("ralph.pipeline.parallel.coordinator")
    assert spec is not None, "ralph.pipeline.parallel.coordinator must exist"

    module = importlib.import_module("ralph.pipeline.parallel.coordinator")
    run_fan_out = getattr(module, "run_fan_out", None)
    assert callable(run_fan_out), "run_fan_out must be defined"
    return cast("RunFanOut", run_fan_out)


def make_unit(unit_id: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Unit {unit_id}",
        dependencies=list(deps or []),
    )


def make_worker_context(
    *,
    log_dir: Path | None = None,
    run_id: str = "default",
    same_workspace: SameWorkspaceContext | None = None,
) -> Any:
    module = importlib.import_module("ralph.pipeline.parallel.coordinator")
    ctx_type = module._WorkerContext
    log = None
    if log_dir is not None:
        log_type = module._WorkerLog
        log = log_type(log_dir=log_dir, run_id=run_id)
    return ctx_type(log=log, same_workspace=same_workspace)


def _seed_artifact(repo_root: Path, unit_id: str) -> None:
    artifact_dir = repo_root / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.json").write_text(
        json.dumps(
            {
                "name": "plan",
                "type": "plan",
                "content": {"summary": "done"},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            }
        )
    )


class RecordingDisplay:
    def __init__(self) -> None:
        self.outputs: dict[str, list[str]] = defaultdict(list)
        self.statuses: dict[str, list[WorkerStatus]] = defaultdict(list)
        self._running_units: set[str] = set()
        self.peak_running = 0

    def emit(self, unit_id: str | None, line: str) -> None:
        if unit_id is None:
            return
        self.outputs[unit_id].append(line)

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        self.statuses[unit_id].append(status)
        if status is WorkerStatus.RUNNING:
            self._running_units.add(unit_id)
        elif status in {
            WorkerStatus.SUCCEEDED,
            WorkerStatus.FAILED,
            WorkerStatus.CANCELLED,
        }:
            self._running_units.discard(unit_id)
        self.peak_running = max(self.peak_running, len(self._running_units))


@dataclass
class _RecordedHandle:
    handle: McpServerHandle
    shutdown_calls: int = 0


class _RecordingMcpFactory:
    def __init__(self) -> None:
        self.sessions: list[object] = []
        self.handles: list[_RecordedHandle] = []

    def build(self, session: object) -> McpServerHandle:
        self.sessions.append(session)
        recorded = _RecordedHandle(
            handle=McpServerHandle(
                endpoint=f"http://127.0.0.1:{10_000 + len(self.handles)}/mcp",
                pid=1000 + len(self.handles),
                shutdown=lambda: None,
            )
        )

        def _shutdown(record: _RecordedHandle = recorded) -> None:
            record.shutdown_calls += 1

        recorded.handle = McpServerHandle(
            endpoint=recorded.handle.endpoint,
            pid=recorded.handle.pid,
            shutdown=_shutdown,
        )
        self.handles.append(recorded)
        return recorded.handle


class _LoggingExecutor:
    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        on_status(WorkerStatus.RUNNING)
        logger.info("worker-log-message")
        on_output("done")
        on_status(WorkerStatus.SUCCEEDED)
        return WorkerResult(
            unit_id=unit.unit_id,
            exit_code=0,
            final_message="done",
            duration_ms=1,
        )


async def test_happy_path_three_units(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    units = (make_unit("unit-a"), make_unit("unit-b"), make_unit("unit-c"))
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    executor = FakeAgentExecutor(
        {
            "unit-a": FakeRun(outputs=["a1", "a2"], exit_code=0, duration_ms=10),
            "unit-b": FakeRun(outputs=["b1", "b2"], exit_code=0, duration_ms=10),
            "unit-c": FakeRun(outputs=["c1", "c2"], exit_code=0, duration_ms=10),
        }
    )
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=executor,
        display=display,
        ctx=make_worker_context(),
    )

    assert events[0] is PipelineEvent.FAN_OUT_STARTED
    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE

    started = [event for event in events if isinstance(event, WorkerStartedEvent)]
    completed = [event for event in events if isinstance(event, WorkerCompletedEvent)]
    failed = [event for event in events if isinstance(event, WorkerFailedEvent)]

    assert {event.unit_id for event in started} == {"unit-a", "unit-b", "unit-c"}
    assert {event.unit_id for event in completed} == {"unit-a", "unit-b", "unit-c"}
    assert failed == []


async def test_failure_cancels_siblings(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    units = (make_unit("unit-a"), make_unit("unit-b"), make_unit("unit-z"))
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    executor = FakeAgentExecutor(
        {
            "unit-a": FakeRun(outputs=["a1", "a2", "a3"], exit_code=0, duration_ms=10),
            "unit-b": FakeRun(outputs=["b1", "b2", "b3"], exit_code=0, duration_ms=10),
            "unit-z": FakeRun(
                outputs=[],
                exit_code=1,
                duration_ms=0,
                raise_on_start=RuntimeError("boom"),
            ),
        }
    )
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=executor,
        display=display,
        ctx=make_worker_context(),
    )

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events
    assert any(
        isinstance(event, WorkerFailedEvent) and event.unit_id == "unit-z" and event.error == "boom"
        for event in events
    )
    assert WorkerStatus.CANCELLED in display.statuses["unit-a"]
    assert WorkerStatus.CANCELLED in display.statuses["unit-b"]


async def test_respects_dag_order(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    units = (
        make_unit("unit-a"),
        make_unit("unit-b", ["unit-a"]),
        make_unit("unit-c", ["unit-b"]),
    )
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    executor = FakeAgentExecutor(
        {
            "unit-a": FakeRun(outputs=["a"], exit_code=0, duration_ms=10),
            "unit-b": FakeRun(outputs=["b"], exit_code=0, duration_ms=10),
            "unit-c": FakeRun(outputs=["c"], exit_code=0, duration_ms=10),
        }
    )
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=executor,
        display=display,
        ctx=make_worker_context(),
    )

    start_order = {
        event.unit_id: index
        for index, event in enumerate(events)
        if isinstance(event, WorkerStartedEvent)
    }

    assert start_order["unit-a"] < start_order["unit-b"] < start_order["unit-c"]


async def test_respects_max_workers_cap(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    units = tuple(make_unit(f"unit-{index}") for index in range(TOTAL_CAP_UNITS))
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=MAX_CAP_WORKERS)
    executor = FakeAgentExecutor(
        {
            unit.unit_id: FakeRun(
                outputs=["line-1", "line-2", "line-3"], exit_code=0, duration_ms=10
            )
            for unit in units
        }
    )
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=executor,
        display=display,
        ctx=make_worker_context(),
    )

    completed = [event for event in events if isinstance(event, WorkerCompletedEvent)]

    assert len(completed) == TOTAL_CAP_UNITS
    assert display.peak_running == MAX_CAP_WORKERS


async def test_empty_work_units(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()

    events = await run_fan_out(
        effect=FanOutDevelopmentEffect(work_units=(), max_workers=2),
        executor=FakeAgentExecutor({}),
        display=RecordingDisplay(),
        ctx=make_worker_context(),
    )

    assert events == [PipelineEvent.FAN_OUT_STARTED, PipelineEvent.ALL_WORKERS_COMPLETE]


async def test_failed_dependency_marks_blocked_unit_failed(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    effect = FanOutDevelopmentEffect(
        work_units=(make_unit("unit-a"), make_unit("unit-b", ["unit-a"])),
        max_workers=2,
    )
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {
                "unit-a": FakeRun(
                    outputs=["boom"],
                    exit_code=1,
                    duration_ms=0,
                    raise_on_start=RuntimeError("unit-a failed"),
                ),
                "unit-b": FakeRun(outputs=["never runs"], exit_code=0, duration_ms=10),
            }
        ),
        display=display,
        ctx=make_worker_context(),
    )

    failed_events = [event for event in events if isinstance(event, WorkerFailedEvent)]

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events
    assert {event.unit_id for event in failed_events} == {"unit-a", "unit-b"}
    assert any(
        event.unit_id == "unit-b" and event.error == "Blocked by failed dependencies: unit-a"
        for event in failed_events
    )


async def test_same_workspace_creates_worker_session_and_shuts_down_mcp(
    tmp_path: Path,
) -> None:
    run_fan_out = _load_run_fan_out()
    unit = WorkUnit(unit_id="unit-a", description="Unit unit-a", allowed_directories=["src"])
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()

    _seed_artifact(tmp_path, "unit-a")

    ctx = make_worker_context(
        same_workspace=SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mcp_factory,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        )
    )

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor({"unit-a": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1)}),
        display=display,
        ctx=ctx,
    )

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE
    assert len(mcp_factory.sessions) == 1
    assert getattr(mcp_factory.sessions[0], "parallel_worker", False) is True
    assert mcp_factory.handles[0].shutdown_calls == 1


async def test_empirical_success_artifact_beats_nonzero_exit(tmp_path: Path) -> None:
    """Same-workspace worker with nonzero exit code succeeds when an artifact is present."""
    run_fan_out = _load_run_fan_out()
    unit = WorkUnit(unit_id="unit-a", description="Unit unit-a", allowed_directories=["src"])
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()

    _seed_artifact(tmp_path, "unit-a")

    ctx = make_worker_context(
        same_workspace=SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mcp_factory,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        )
    )

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=1, duration_ms=1)}
        ),
        display=display,
        ctx=ctx,
    )

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE
    assert display.statuses["unit-a"][-1] is WorkerStatus.SUCCEEDED


async def test_empirical_failure_no_artifact_despite_zero_exit(tmp_path: Path) -> None:
    """Same-workspace worker with zero exit code fails when no artifact is submitted."""
    run_fan_out = _load_run_fan_out()
    unit = WorkUnit(unit_id="unit-a", description="Unit unit-a", allowed_directories=["src"])
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()

    ctx = make_worker_context(
        same_workspace=SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mcp_factory,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        )
    )

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {"unit-a": FakeRun(outputs=["done"], exit_code=0, duration_ms=1)}
        ),
        display=display,
        ctx=ctx,
    )

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events
    assert display.statuses["unit-a"][-1] is WorkerStatus.FAILED
    assert any(
        isinstance(event, WorkerFailedEvent)
        and event.unit_id == "unit-a"
        and "no worker-local artifact" in event.error
        for event in events
    )


async def test_activity_router_is_passed_to_subprocess_worker_executor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = importlib.import_module("ralph.pipeline.parallel.coordinator")
    run_fan_out = _load_run_fan_out()
    unit = WorkUnit(unit_id="unit-a", description="Unit unit-a", allowed_directories=["src"])
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()
    router = ActivityRouter()
    recorded_activity_routers: list[ActivityRouter | None] = []
    coordinator = module.ParallelCoordinator(activity_router=router)

    assert coordinator.activity_router is router

    class _RecordingSubprocessExecutor:
        def __init__(
            self,
            *_args: Any,
            activity_router: ActivityRouter | None = None,
            **_kwargs: Any,
        ) -> None:
            recorded_activity_routers.append(activity_router)

        async def run(
            self,
            unit: WorkUnit,
            *,
            on_output: Callable[[str], None],
            on_status: Callable[[WorkerStatus], None],
        ) -> WorkerResult:
            on_status(WorkerStatus.RUNNING)
            on_output("done")
            on_status(WorkerStatus.SUCCEEDED)
            return WorkerResult(
                unit_id=unit.unit_id,
                exit_code=0,
                final_message="done",
                duration_ms=1,
            )

    monkeypatch.setattr(
        module.subprocess_executor,
        "SubprocessAgentExecutor",
        _RecordingSubprocessExecutor,
    )

    class _FakeDynamicBindingMcpServerFactory:
        def __init__(self, *, workspace: object) -> None:
            del workspace

        def build(self, session: object) -> McpServerHandle:
            return mcp_factory.build(session)

    monkeypatch.setattr(
        module.factory_impl,
        "DynamicBindingMcpServerFactory",
        _FakeDynamicBindingMcpServerFactory,
    )

    _seed_artifact(tmp_path, "unit-a")

    ctx = make_worker_context(
        same_workspace=SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mcp_factory,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            executor_command=("python", "-m", "ralph"),
        )
    )

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor({}),
        display=display,
        ctx=ctx,
        activity_router=router,
    )

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE
    assert recorded_activity_routers == [router]


async def test_worker_logs_are_routed_to_per_worker_sink(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    unit = make_unit("unit-a")
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=_LoggingExecutor(),
        display=display,
        ctx=make_worker_context(log_dir=tmp_path / "logs", run_id="run-logging"),
    )

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE
    log_path = tmp_path / "logs" / "run-logging" / "workers" / "unit-unit-a.log"
    assert "worker-log-message" in log_path.read_text()


@pytest.mark.asyncio
async def test_subprocess_worker_receives_ralph_worker_artifact_dir(
    tmp_path: Path, monkeypatch: Any
) -> None:
    module = importlib.import_module("ralph.pipeline.parallel.coordinator")
    run_fan_out = _load_run_fan_out()
    unit = WorkUnit(unit_id="unit-b", description="Unit unit-b", allowed_directories=["src"])
    effect = FanOutDevelopmentEffect(work_units=(unit,), max_workers=1)
    display = RecordingDisplay()
    mcp_factory = _RecordingMcpFactory()
    recorded_extra_envs: list[dict[str, str]] = []

    class _RecordingSubprocessExecutor:
        def __init__(
            self,
            *_args: Any,
            extra_env: dict[str, str] | None = None,
            **_kwargs: Any,
        ) -> None:
            recorded_extra_envs.append(dict(extra_env or {}))

        async def run(
            self,
            unit: WorkUnit,
            *,
            on_output: Callable[[str], None],
            on_status: Callable[[WorkerStatus], None],
        ) -> WorkerResult:
            on_status(WorkerStatus.RUNNING)
            on_output("done")
            on_status(WorkerStatus.SUCCEEDED)
            return WorkerResult(
                unit_id=unit.unit_id,
                exit_code=0,
                final_message="done",
                duration_ms=1,
            )

    monkeypatch.setattr(
        module.subprocess_executor,
        "SubprocessAgentExecutor",
        _RecordingSubprocessExecutor,
    )

    class _FakeDynamicBindingMcpServerFactory:
        def __init__(self, *, workspace: object) -> None:
            del workspace

        def build(self, session: object) -> McpServerHandle:
            return mcp_factory.build(session)

    monkeypatch.setattr(
        module.factory_impl,
        "DynamicBindingMcpServerFactory",
        _FakeDynamicBindingMcpServerFactory,
    )

    _seed_artifact(tmp_path, "unit-b")

    ctx = make_worker_context(
        same_workspace=SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mcp_factory,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            executor_command=("python", "-m", "ralph"),
        )
    )

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor({}),
        display=display,
        ctx=ctx,
    )

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE
    assert len(recorded_extra_envs) == 1
    env = recorded_extra_envs[0]
    assert "RALPH_WORKER_ARTIFACT_DIR" in env, (
        "SubprocessAgentExecutor must receive RALPH_WORKER_ARTIFACT_DIR in extra_env"
    )
    expected_suffix = str(tmp_path / ".agent" / "workers" / "unit-b" / "artifacts")
    assert env["RALPH_WORKER_ARTIFACT_DIR"] == expected_suffix, (
        f"RALPH_WORKER_ARTIFACT_DIR must point to the per-worker artifact dir, "
        f"got: {env['RALPH_WORKER_ARTIFACT_DIR']!r}"
    )


@pytest.mark.asyncio
async def test_fan_out_start_log_line(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    # Use empty work_units so fan_out returns immediately after emitting the log.
    effect = FanOutDevelopmentEffect(work_units=(), max_workers=2)
    display = RecordingDisplay()
    ctx = make_worker_context(
        same_workspace=SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=_RecordingMcpFactory(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            executor_command=("python", "-m", "ralph"),
        )
    )

    log_records: list[str] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(self.format(record))

    handler = _CapturingHandler()
    handler_id = loguru.logger.add(handler, format="{message}", level="INFO")

    try:
        events = await run_fan_out(
            effect=effect,
            executor=FakeAgentExecutor({}),
            display=display,
            ctx=ctx,
        )
    finally:
        loguru.logger.remove(handler_id)

    assert events[-1] is PipelineEvent.ALL_WORKERS_COMPLETE

    fan_out_logs = [r for r in log_records if "fan-out start" in r]
    assert fan_out_logs, "Expected a 'fan-out start' log line from run_fan_out"
    log_line = fan_out_logs[0]
    assert "same_workspace" in log_line, f"Log must mention mode=same_workspace: {log_line!r}"
    assert "units=0" in log_line, f"Log must mention units count: {log_line!r}"
    assert str(tmp_path / ".agent" / "workers") in log_line, (
        f"Log must mention namespace_root path: {log_line!r}"
    )
