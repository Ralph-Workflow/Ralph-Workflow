"""Integration tests for partial-failure reporting in parallel fan-out.

Verifies that when some workers succeed before a dependent worker fails,
per-unit status is reported correctly for all workers.
"""

from __future__ import annotations

import asyncio

from ralph.config.enums import PHASE_DEVELOPMENT
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


def _make_work_unit(uid: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        dependencies=list(deps or []),
    )


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


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


class TestPartialFailureReporting:
    def test_successful_units_report_complete_before_dependent_fails(self) -> None:
        """Units B and C complete successfully before A (which depends on them) fails.

        Because A depends on B and C, the coordinator runs B and C first.
        After both succeed, A is scheduled. A then fails. The result is that
        B and C show WorkerCompletedEvent and A shows WorkerFailedEvent — partial
        success with per-unit status preserved.
        """
        units = (
            _make_work_unit("unit-b"),
            _make_work_unit("unit-c"),
            _make_work_unit("unit-a", deps=["unit-b", "unit-c"]),
        )
        runs = {
            "unit-b": FakeRun(outputs=["b-done"], exit_code=0, duration_ms=1),
            "unit-c": FakeRun(outputs=["c-done"], exit_code=0, duration_ms=1),
            "unit-a": FakeRun(
                outputs=[],
                exit_code=1,
                duration_ms=1,
                raise_on_start=RuntimeError("unit-a failed"),
            ),
        }
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

        events = _run_fan_out(effect, state, runs)

        completed_ids = {
            event.unit_id for event in events if isinstance(event, WorkerCompletedEvent)
        }
        failed_ids = {event.unit_id for event in events if isinstance(event, WorkerFailedEvent)}

        # B and C succeed, A fails
        assert "unit-b" in completed_ids
        assert "unit-c" in completed_ids
        assert "unit-a" in failed_ids
        # Not ALL_WORKERS_COMPLETE because a worker failed
        assert PipelineEvent.ALL_WORKERS_COMPLETE not in events

    def test_per_unit_status_in_pipeline_state_after_partial_failure(self) -> None:
        """Reducer correctly records per-unit status when some workers succeed and one fails."""
        units = (
            _make_work_unit("unit-b"),
            _make_work_unit("unit-c"),
            _make_work_unit("unit-a", deps=["unit-b", "unit-c"]),
        )
        runs = {
            "unit-b": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1),
            "unit-c": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1),
            "unit-a": FakeRun(
                outputs=[],
                exit_code=1,
                duration_ms=1,
                raise_on_start=RuntimeError("unit-a failed"),
            ),
        }
        initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=3)

        events = _run_fan_out(effect, initial_state, runs)

        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event)

        assert reduced_state.worker_states["unit-b"].status == WorkerStatus.SUCCEEDED
        assert reduced_state.worker_states["unit-c"].status == WorkerStatus.SUCCEEDED
        assert reduced_state.worker_states["unit-a"].status == WorkerStatus.FAILED

    def test_failure_error_message_attributed_to_correct_worker(self) -> None:
        """The failure error message belongs to the correct worker, not siblings."""
        units = (
            _make_work_unit("unit-b"),
            _make_work_unit("unit-a", deps=["unit-b"]),
        )
        runs = {
            "unit-b": FakeRun(outputs=["ok"], exit_code=0, duration_ms=1),
            "unit-a": FakeRun(
                outputs=[],
                exit_code=1,
                duration_ms=1,
                raise_on_start=RuntimeError("specific error for unit-a"),
            ),
        }
        effect = FanOutDevelopmentEffect(work_units=units, max_workers=2)
        state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)

        events = _run_fan_out(effect, state, runs)

        failed_events = [event for event in events if isinstance(event, WorkerFailedEvent)]
        unit_a_failures = [e for e in failed_events if e.unit_id == "unit-a"]
        assert len(unit_a_failures) == 1
        assert "specific error for unit-a" in unit_a_failures[0].error

        completed_events = [event for event in events if isinstance(event, WorkerCompletedEvent)]
        assert any(e.unit_id == "unit-b" for e in completed_events)
