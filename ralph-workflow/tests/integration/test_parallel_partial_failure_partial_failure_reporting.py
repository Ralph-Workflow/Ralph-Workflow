from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from tests.integration._fake_display_reporting import _FakeDisplay


def _make_work_unit(uid: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        dependencies=list(deps or []),
        allowed_directories=[f"src/{uid}"],
    )


def _run_fan_out(effect: FanOutEffect, runs: dict[str, FakeRun]) -> list[object]:
    return asyncio.run(
        coordinator.run_fan_out(
            effect=effect,
            executor=FakeAgentExecutor(runs),
            display=_FakeDisplay(),
        )
    )


def _make_policy_bundle() -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    return bundle


class TestPartialFailureReporting:
    def test_successful_units_report_complete_before_dependent_fails(self) -> None:
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
        effect = FanOutEffect(work_units=units, max_workers=3)

        events = _run_fan_out(effect, runs)

        completed_ids = {
            event.unit_id for event in events if isinstance(event, WorkerCompletedEvent)
        }
        failed_ids = {event.unit_id for event in events if isinstance(event, WorkerFailedEvent)}

        assert "unit-b" in completed_ids
        assert "unit-c" in completed_ids
        assert "unit-a" in failed_ids
        assert PipelineEvent.ALL_WORKERS_COMPLETE not in events

    def test_per_unit_status_in_pipeline_state_after_partial_failure(self) -> None:
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
        initial_state = PipelineState(phase="development", work_units=units)
        effect = FanOutEffect(work_units=units, max_workers=3)

        events = _run_fan_out(effect, runs)

        reduced_state = initial_state
        for event in events:
            reduced_state, _ = reducer_reduce(reduced_state, event)

        assert reduced_state.worker_states["unit-b"].status == WorkerStatus.SUCCEEDED
        assert reduced_state.worker_states["unit-c"].status == WorkerStatus.SUCCEEDED
        assert reduced_state.worker_states["unit-a"].status == WorkerStatus.FAILED

    def test_failure_error_message_attributed_to_correct_worker(self) -> None:
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
        effect = FanOutEffect(work_units=units, max_workers=2)

        events = _run_fan_out(effect, runs)

        failed_events = [event for event in events if isinstance(event, WorkerFailedEvent)]
        unit_a_failures = [e for e in failed_events if e.unit_id == "unit-a"]
        assert len(unit_a_failures) == 1
        assert "specific error for unit-a" in unit_a_failures[0].error

        completed_events = [event for event in events if isinstance(event, WorkerCompletedEvent)]
        assert any(e.unit_id == "unit-b" for e in completed_events)

    def test_one_success_one_failure_routes_to_phase_failed_with_sorted_attribution(
        self,
    ) -> None:
        units = (
            _make_work_unit("unit-alpha"),
            _make_work_unit("unit-beta"),
        )
        events: list[Event] = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerStartedEvent(unit_id="unit-alpha"),
            WorkerStartedEvent(unit_id="unit-beta"),
            WorkerCompletedEvent(unit_id="unit-alpha", exit_code=0),
            WorkerFailedEvent(unit_id="unit-beta", exit_code=1, error="beta crashed"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        initial_state = PipelineState(phase="development", work_units=units)
        state = initial_state
        bundle = _make_policy_bundle()
        for event in events:
            state, _ = reducer_reduce(state, event, bundle.pipeline)

        assert state.phase == "failed_terminal", (
            f"Expected 'failed_terminal' after partial failure + ALL_WORKERS_COMPLETE, "
            f"got {state.phase!r}"
        )
        assert state.last_error is not None, "last_error must be set after worker failure"
        assert "unit-beta" in (state.last_error or ""), (
            f"last_error must name the failing unit, got: {state.last_error!r}"
        )
        assert "unit-alpha" not in (state.last_error or ""), (
            f"last_error must not name the succeeding unit, got: {state.last_error!r}"
        )

    def test_two_failures_sorted_in_error_attribution(self) -> None:
        units = (
            _make_work_unit("unit-alpha"),
            _make_work_unit("unit-beta"),
            _make_work_unit("unit-gamma"),
        )
        events: list[Event] = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerStartedEvent(unit_id="unit-alpha"),
            WorkerStartedEvent(unit_id="unit-beta"),
            WorkerStartedEvent(unit_id="unit-gamma"),
            WorkerCompletedEvent(unit_id="unit-alpha", exit_code=0),
            WorkerFailedEvent(unit_id="unit-beta", exit_code=1, error="beta failed"),
            WorkerFailedEvent(unit_id="unit-gamma", exit_code=1, error="gamma failed"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        initial_state = PipelineState(phase="development", work_units=units)
        state = initial_state
        bundle = _make_policy_bundle()
        for event in events:
            state, _ = reducer_reduce(state, event, bundle.pipeline)

        assert state.phase == "failed_terminal"
        assert state.last_error is not None
        assert "unit-beta" in (state.last_error or ""), (
            f"last_error must name unit-beta, got: {state.last_error!r}"
        )
        assert "unit-gamma" in (state.last_error or ""), (
            f"last_error must name unit-gamma, got: {state.last_error!r}"
        )
        last_error = state.last_error or ""
        assert last_error.index("unit-beta") < last_error.index("unit-gamma"), (
            f"unit-beta must appear before unit-gamma in last_error: {last_error!r}"
        )
