"""Integration tests for partial-failure reporting in parallel fan-out.

Verifies that when some workers succeed before a dependent worker fails,
per-unit status is reported correctly for all workers.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutDevelopmentEffect
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
from ralph.policy.models import PhaseParallelization
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.scope import WorkspaceScope


def _make_work_unit(uid: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        dependencies=list(deps or []),
        allowed_directories=[f"src/{uid}"],
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


def _make_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=True)
    dev_phase = MagicMock(requires_commit=False, drain="development")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {PHASE_DEVELOPMENT: dev_phase}
    bundle.pipeline.recovery.failed_route = PHASE_FAILED
    bundle.agents.agent_drains = {
        "development": MagicMock(chain="developer"),
    }
    bundle.agents.agent_chains = {
        "developer": MagicMock(agents=["developer"]),
    }
    return bundle


def _seed_artifact(repo_root: Path, unit_id: str) -> None:
    """Pre-populate worker-local artifact evidence."""
    artifact_dir = repo_root / ".agent" / "workers" / unit_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "development_result.json").write_text(
        json.dumps(
            {
                "name": "development_result",
                "type": "development_result",
                "content": {"summary": f"Worker {unit_id} done", "changes": []},
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
                "metadata": {},
            }
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

    def test_one_success_one_failure_routes_to_phase_failed_with_sorted_attribution(
        self,
    ) -> None:
        """ALL_WORKERS_COMPLETE after partial failure routes to PHASE_FAILED with attribution.

        The coordinator does not emit ALL_WORKERS_COMPLETE when workers fail, but the
        reducer's contract must still be correct: when ALL_WORKERS_COMPLETE is processed
        and there are failed workers, the final state must be PHASE_FAILED with sorted
        attribution in last_error. This test exercises that reducer contract directly.

        Asserts:
        - Final reduced state is PHASE_FAILED
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

        initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
        state = initial_state
        bundle = _make_policy_bundle()
        for event in events:
            state, _ = reducer_reduce(state, event, bundle.pipeline)

        assert state.phase == PHASE_FAILED, (
            f"Expected PHASE_FAILED after partial failure + ALL_WORKERS_COMPLETE, "
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

        initial_state = PipelineState(phase=PHASE_DEVELOPMENT, work_units=units)
        state = initial_state
        bundle = _make_policy_bundle()
        for event in events:
            state, _ = reducer_reduce(state, event, bundle.pipeline)

        assert state.phase == PHASE_FAILED
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


class TestPartialFailureHandoffContent:
    """Runner-level tests: DEVELOPMENT_RESULT.md handoff content on partial failure.

    These tests verify that when 1 of 2 workers fails, the resulting handoff
    artifact contains both unit_ids, marks the right worker as failed, and
    reports phase state as PHASE_FAILED (not PHASE_DEVELOPMENT_ANALYSIS).
    """

    def test_partial_failure_development_result_md_contains_both_unit_ids(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When unit-b fails, DEVELOPMENT_RESULT.md must name BOTH unit-a and unit-b.

        Asserts:
        - .agent/DEVELOPMENT_RESULT.md exists and contains both unit_ids
        - unit-b (the failed one) is named in the failure context
        - unit-a (the success) is present and not blamed
        - any_failed: true and all_succeeded: false
        """
        from ralph.pipeline import checkpoint as ckpt  # noqa: PLC0415

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")

        _seed_artifact(tmp_path, "unit-a")  # unit-b deliberately has no artifact

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="unit-b: no artifact"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutDevelopmentEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        final_state = runner_module._execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        # State must be PHASE_FAILED — never PHASE_DEVELOPMENT_ANALYSIS on partial failure
        assert final_state.phase == PHASE_FAILED, (
            f"Partial failure must produce PHASE_FAILED, got {final_state.phase!r}. "
            "PHASE_DEVELOPMENT_ANALYSIS must only be reached when ALL workers succeed."
        )

        # DEVELOPMENT_RESULT.md must contain both unit_ids
        handoff_path = tmp_path / ".agent" / "DEVELOPMENT_RESULT.md"
        assert handoff_path.exists(), (
            ".agent/DEVELOPMENT_RESULT.md must be written on partial failure"
        )
        content = handoff_path.read_text()

        assert "unit-a" in content, (
            "DEVELOPMENT_RESULT.md must name unit-a (the successful worker)"
        )
        assert "unit-b" in content, (
            "DEVELOPMENT_RESULT.md must name unit-b (the failed worker)"
        )
        assert "any_failed: true" in content, (
            "DEVELOPMENT_RESULT.md must report any_failed: true"
        )
        assert "all_succeeded: false" in content, (
            "DEVELOPMENT_RESULT.md must report all_succeeded: false"
        )

    def test_parallel_development_summary_json_has_per_unit_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """parallel_development_summary.json lists per-unit status on partial failure.

        Asserts:
        - .agent/artifacts/parallel_development_summary.json exists
        - any_failed = true
        - all_succeeded = false
        - workers list contains entries for both unit-a and unit-b
        - unit-a entry has status 'succeeded'
        - unit-b entry has a non-success status
        """
        from ralph.pipeline import checkpoint as ckpt  # noqa: PLC0415

        unit_a = _make_work_unit("unit-a")
        unit_b = _make_work_unit("unit-b")

        _seed_artifact(tmp_path, "unit-a")

        scope = WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))
        initial_state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            work_units=(unit_a, unit_b),
        )

        partial_events = [
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="unit-b failed"),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ]

        async def _fake_run_fan_out(**kwargs: object) -> list[object]:
            return partial_events

        monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
        monkeypatch.setattr(ckpt, "save", lambda state: None)

        bundle = _make_policy_bundle(max_workers=2)
        effect = FanOutDevelopmentEffect(
            work_units=(unit_a, unit_b),
            max_workers=2,
            run_post_fanout_verification=False,
        )

        runner_module._execute_fan_out_sync(
            effect=effect,
            state=initial_state,
            display=_FakeDisplay(),  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            policy_bundle=bundle,
            workspace_scope=scope,
        )

        summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.json"
        assert summary_path.exists(), (
            ".agent/artifacts/parallel_development_summary.json must be written after fan-out"
        )
        summary = json.loads(summary_path.read_text())

        assert summary["any_failed"] is True, (
            f"parallel_development_summary.json must have any_failed=true, got: {summary!r}"
        )
        assert summary["all_succeeded"] is False, (
            f"parallel_development_summary.json must have all_succeeded=false, got: {summary!r}"
        )

        workers_by_id = {w["unit_id"]: w for w in summary["workers"]}
        assert "unit-a" in workers_by_id, "unit-a must appear in workers list"
        assert "unit-b" in workers_by_id, "unit-b must appear in workers list"

        assert workers_by_id["unit-a"]["status"] == "succeeded", (
            f"unit-a must be succeeded, got: {workers_by_id['unit-a']!r}"
        )
        assert workers_by_id["unit-b"]["status"] != "succeeded", (
            f"unit-b must not be succeeded, got: {workers_by_id['unit-b']!r}"
        )
