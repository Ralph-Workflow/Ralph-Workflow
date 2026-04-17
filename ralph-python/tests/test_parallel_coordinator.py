from __future__ import annotations

import importlib
import importlib.util
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.state import PipelineState
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
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
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
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
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
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
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
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
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
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
    )

    assert events == [PipelineEvent.FAN_OUT_STARTED, PipelineEvent.ALL_WORKERS_COMPLETE]


async def test_nonzero_exit_emits_worker_failed_event(tmp_path: Path) -> None:
    run_fan_out = _load_run_fan_out()
    effect = FanOutDevelopmentEffect(work_units=(make_unit("unit-a"),), max_workers=1)
    display = RecordingDisplay()

    events = await run_fan_out(
        effect=effect,
        executor=FakeAgentExecutor(
            {
                "unit-a": FakeRun(outputs=["boom"], exit_code=1, duration_ms=10),
            }
        ),
        display=display,
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
    )

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events
    assert any(
        isinstance(event, WorkerFailedEvent) and event.unit_id == "unit-a" and event.exit_code == 1
        for event in events
    )
    assert display.statuses["unit-a"][-1] is WorkerStatus.FAILED


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
                "unit-a": FakeRun(outputs=["boom"], exit_code=1, duration_ms=10),
                "unit-b": FakeRun(outputs=["never runs"], exit_code=0, duration_ms=10),
            }
        ),
        display=display,
        checkpoint_path=tmp_path / "checkpoint.json",
        state=PipelineState(),
    )

    failed_events = [event for event in events if isinstance(event, WorkerFailedEvent)]

    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events
    assert {event.unit_id for event in failed_events} == {"unit-a", "unit-b"}
    assert any(
        event.unit_id == "unit-b" and event.error == "Blocked by failed dependencies: unit-a"
        for event in failed_events
    )
