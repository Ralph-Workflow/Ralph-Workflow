from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.agents.executor import AgentExecutor
    from ralph.display.parallel_display import ParallelDisplay

from ralph.agents.worker_result import WorkerResult
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import Event, PipelineEvent, WorkerCompletedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(unit_id=uid, description=f"Work unit {uid}", allowed_directories=[f"src/{uid}"])


def _run_fan_out(
    effect: FanOutEffect,
    runs: dict[str, FakeRun],
) -> list[Event]:
    return asyncio.run(
        coordinator.run_fan_out(
            effect=effect,
            executor=FakeAgentExecutor(runs),
            display=cast("ParallelDisplay", _FakeDisplay()),
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
    started: list[str] = []
    release_workers = asyncio.Event()
    second_started = asyncio.Event()

    class _ParallelProofExecutor:
        async def run(
            self,
            unit: WorkUnit,
            *,
            on_output: object,
            on_status: object,
        ) -> WorkerResult:
            del on_output, on_status
            started.append(unit.unit_id)
            if unit.unit_id == "unit-A":
                await asyncio.wait_for(second_started.wait(), timeout=5.0)
            elif unit.unit_id == "unit-B":
                second_started.set()
            await asyncio.wait_for(release_workers.wait(), timeout=5.0)
            return WorkerResult(
                unit_id=unit.unit_id,
                exit_code=0,
                final_message="done",
                duration_ms=1,
            )

    async def _exercise() -> list[Event]:
        task = asyncio.create_task(
            coordinator.run_fan_out(
                effect=FanOutEffect(work_units=units, max_workers=3),
                executor=cast("AgentExecutor", _ParallelProofExecutor()),
                display=cast("ParallelDisplay", _FakeDisplay()),
            )
        )
        await asyncio.wait_for(second_started.wait(), timeout=5.0)
        release_workers.set()
        return await task

    events = asyncio.run(_exercise())

    completed_events = [event for event in events if isinstance(event, WorkerCompletedEvent)]
    completed_ids = {event.unit_id for event in completed_events}

    assert events[0] == PipelineEvent.FAN_OUT_STARTED
    assert events[-1] == PipelineEvent.ALL_WORKERS_COMPLETE
    assert completed_ids == {"unit-A", "unit-B", "unit-C"}
    assert all(event.exit_code == 0 for event in completed_events)
    assert started[:2] == ["unit-A", "unit-B"]


def _fan_out_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="development_analysis"),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


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
    initial_state = PipelineState(phase="development", work_units=units)
    effect = FanOutEffect(work_units=units, max_workers=3)
    policy = _fan_out_policy()

    events = _run_fan_out(effect, runs)

    reduced_state = initial_state
    for event in events:
        reduced_state, _ = reducer_reduce(reduced_state, event, policy)

    assert PipelineEvent.ALL_WORKERS_COMPLETE in events
    assert reduced_state.phase == "development_analysis"
    assert reduced_state.worker_states["unit-A"].status == WorkerStatus.SUCCEEDED
    assert reduced_state.worker_states["unit-B"].status == WorkerStatus.SUCCEEDED
    assert reduced_state.worker_states["unit-C"].status == WorkerStatus.SUCCEEDED

