from __future__ import annotations

import asyncio

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_MERGE_INTEGRATION
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
)
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(unit_id=uid, description=f"Work unit {uid}")


def _long_running_outputs() -> list[str]:
    return [f"tick-{index}" for index in range(200)]


def _run_fan_out(
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    runs: dict[str, FakeRun],
) -> list[Event]:
    return asyncio.run(
        coordinator.run_fan_out(
            effect=effect,
            executor=FakeAgentExecutor(runs),
            display=_FakeDisplay(),  # type: ignore[arg-type]
        )
    )


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


def test_one_failure_cancels_all() -> None:
    units = (
        _make_work_unit("unit-A"),
        _make_work_unit("unit-B"),
        _make_work_unit("unit-C"),
    )
    runs = {
        "unit-A": FakeRun(outputs=[], exit_code=1, duration_ms=1),
        "unit-B": FakeRun(outputs=_long_running_outputs(), exit_code=0, duration_ms=1000),
        "unit-C": FakeRun(outputs=_long_running_outputs(), exit_code=0, duration_ms=1000),
    }
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

    events = _run_fan_out(effect, state, runs)

    failed_events = [event for event in events if isinstance(event, WorkerFailedEvent)]
    completed_events = [event for event in events if isinstance(event, WorkerCompletedEvent)]
    failed_ids = {event.unit_id for event in failed_events}
    completed_ids = {event.unit_id for event in completed_events}

    assert events[0] == PipelineEvent.FAN_OUT_STARTED
    assert failed_ids == {"unit-A", "unit-B", "unit-C"}
    assert completed_ids.isdisjoint({"unit-B", "unit-C"})
    assert all(event.unit_id in failed_ids for event in failed_events)
    assert any(event.unit_id == "unit-A" and event.exit_code == 1 for event in failed_events)
    assert any(
        event.unit_id in {"unit-B", "unit-C"}
        and event.error == "Cancelled because another worker failed"
        for event in failed_events
    )
    assert PipelineEvent.ALL_WORKERS_COMPLETE not in events


def test_failed_state_transitions_reflect_failure() -> None:
    units = (
        _make_work_unit("unit-A"),
        _make_work_unit("unit-B"),
        _make_work_unit("unit-C"),
    )
    runs = {
        "unit-A": FakeRun(outputs=[], exit_code=1, duration_ms=1),
        "unit-B": FakeRun(outputs=_long_running_outputs(), exit_code=0, duration_ms=1000),
        "unit-C": FakeRun(outputs=_long_running_outputs(), exit_code=0, duration_ms=1000),
    }
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

    events = _run_fan_out(effect, initial_state, runs)

    reduced_state = initial_state
    for event in events:
        reduced_state, _ = reducer_reduce(reduced_state, event)

    assert reduced_state.phase == PHASE_DEVELOPMENT
    assert reduced_state.phase != PHASE_MERGE_INTEGRATION
    assert reduced_state.worker_states["unit-A"].status == WorkerStatus.FAILED
    assert reduced_state.worker_states["unit-B"].status == WorkerStatus.FAILED
    assert reduced_state.worker_states["unit-C"].status == WorkerStatus.FAILED
    assert (
        reduced_state.worker_states["unit-B"].error_message
        == "Cancelled because another worker failed"
    )
    assert (
        reduced_state.worker_states["unit-C"].error_message
        == "Cancelled because another worker failed"
    )
