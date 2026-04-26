from __future__ import annotations

import asyncio

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_DEVELOPMENT_ANALYSIS
from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import Event, PipelineEvent, WorkerCompletedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(unit_id=uid, description=f"Work unit {uid}")


def _run_fan_out(
    effect: FanOutDevelopmentEffect,
    state: PipelineState,
    runs: dict[str, FakeRun],
) -> list[Event]:
    return asyncio.run(
        coordinator.run_fan_out(
            effect=effect,
            executor=FakeAgentExecutor(runs),
            display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        )
    )


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


def test_three_workers_all_succeed() -> None:
    units = (
        _make_work_unit("unit-A"),
        _make_work_unit("unit-B"),
        _make_work_unit("unit-C"),
    )
    runs = {
        unit.unit_id: FakeRun(outputs=[f"done-{unit.unit_id}"], exit_code=0, duration_ms=1)
        for unit in units
    }
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
    state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

    events = _run_fan_out(effect, state, runs)

    completed_events = [event for event in events if isinstance(event, WorkerCompletedEvent)]
    completed_ids = {event.unit_id for event in completed_events}

    assert events[0] == PipelineEvent.FAN_OUT_STARTED
    assert events[-1] == PipelineEvent.ALL_WORKERS_COMPLETE
    assert completed_ids == {"unit-A", "unit-B", "unit-C"}
    assert all(event.exit_code == 0 for event in completed_events)
    assert all(event.commit_sha == "" for event in completed_events)


def test_happy_path_state_transitions() -> None:
    units = (
        _make_work_unit("unit-A"),
        _make_work_unit("unit-B"),
        _make_work_unit("unit-C"),
    )
    runs = {
        unit.unit_id: FakeRun(outputs=[f"done-{unit.unit_id}"], exit_code=0, duration_ms=1)
        for unit in units
    }
    initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
    effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)

    events = _run_fan_out(effect, initial_state, runs)

    reduced_state = initial_state
    for event in events:
        reduced_state, _ = reducer_reduce(reduced_state, event)

    assert PipelineEvent.ALL_WORKERS_COMPLETE in events
    assert reduced_state.phase == PHASE_DEVELOPMENT_ANALYSIS
    assert reduced_state.worker_states["unit-A"].status == WorkerStatus.SUCCEEDED
    assert reduced_state.worker_states["unit-B"].status == WorkerStatus.SUCCEEDED
    assert reduced_state.worker_states["unit-C"].status == WorkerStatus.SUCCEEDED
