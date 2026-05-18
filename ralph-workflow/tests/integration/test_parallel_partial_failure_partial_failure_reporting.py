"""Integration tests for partial-failure reporting in parallel fan-out.

Verifies that when some workers succeed before a dependent worker fails,
per-unit status is reported correctly for all workers.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from rich.console import Console

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

if TYPE_CHECKING:
    from ralph.display.parallel_display import ParallelDisplay


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
            display=cast("ParallelDisplay", _FakeDisplay()),
        )
    )


def _make_policy_bundle() -> MagicMock:
    bundle = MagicMock()
    bundle.pipeline.recovery.failed_route = "failed_terminal"
    return bundle


class _FakeDisplay:
    def __init__(self) -> None:
        self.console = Console(file=io.StringIO(), force_terminal=False, color_system=None)

    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status


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
        effect = FanOutEffect(work_units=units, max_workers=3)

        events = _run_fan_out(effect, runs)

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
        """ALL_WORKERS_COMPLETE after partial failure routes to "failed" with attribution.

        The coordinator does not emit ALL_WORKERS_COMPLETE when workers fail, but the
        reducer's contract must still be correct: when ALL_WORKERS_COMPLETE is processed
        and there are failed workers, the final state must be "failed" with sorted
        attribution in last_error. This test exercises that reducer contract directly.

        Asserts:
        - Final reduced state is "failed"
        - last_error contains the failing unit's id (unit-beta)
        - last_error does NOT contain the succeeding unit's id (unit-alpha)
        """
        units = (
            _make_work_unit("unit-alpha"),
            _make_work_unit("unit-beta"),
        )
        # Build a synthetic event sequence: alpha succeeds, beta fails, then ALL_WORKERS_COMPLETE.
        # This mirrors what the reducer would process if the coordinator/runner synthesized
        # ALL_WORKERS_COMPLETE after accumulating per-worker failure evidence.
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
        # The succeeding unit must not be blamed
        assert "unit-alpha" not in (state.last_error or ""), (
            f"last_error must not name the succeeding unit, got: {state.last_error!r}"
        )

    def test_two_failures_sorted_in_error_attribution(self) -> None:
        """When two workers fail, last_error lists them alphabetically by unit_id.

        Uses a synthetic event sequence (applying ALL_WORKERS_COMPLETE manually)
        to test the reducer's attribution contract directly.
        """
        units = (
            _make_work_unit("unit-alpha"),
            _make_work_unit("unit-beta"),
            _make_work_unit("unit-gamma"),
        )
        # Alpha succeeds; beta and gamma fail; then ALL_WORKERS_COMPLETE.
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
        # Both failing units must be named; the reducer sorts failed_unit_ids alphabetically
        assert "unit-beta" in (state.last_error or ""), (
            f"last_error must name unit-beta, got: {state.last_error!r}"
        )
        assert "unit-gamma" in (state.last_error or ""), (
            f"last_error must name unit-gamma, got: {state.last_error!r}"
        )
        # beta appears before gamma in alphabetical order
        last_error = state.last_error or ""
        assert last_error.index("unit-beta") < last_error.index("unit-gamma"), (
            f"unit-beta must appear before unit-gamma in last_error: {last_error!r}"
        )
