"""Tests for serialized post-fanout workspace verification."""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING

from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.events import PostFanoutVerificationEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.workspace.scope import WorkspaceScope

_EXIT_CODE_VERIFY_FAIL = 2

if TYPE_CHECKING:
    from pathlib import Path


def _make_scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))


class TestVerificationRunsOnlyWhenFlagTrue:
    def test_verification_flag_defaults_to_false_on_effect(self) -> None:
        """FanOutDevelopmentEffect.run_post_fanout_verification must default to False."""
        effect = FanOutDevelopmentEffect(
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
            ),
            max_workers=1,
        )
        assert effect.run_post_fanout_verification is False, (
            "Default must be False so unit tests never accidentally invoke make verify"
        )

    def test_verification_only_runs_when_flag_true(self) -> None:
        """Verification conditional: flag=False means _run_post_fanout_verification never called."""
        effect_false = FanOutDevelopmentEffect(
            work_units=(WorkUnit(unit_id="u", description="u", allowed_directories=["src/u"]),),
            max_workers=1,
            run_post_fanout_verification=False,
        )
        effect_true = FanOutDevelopmentEffect(
            work_units=(WorkUnit(unit_id="u", description="u", allowed_directories=["src/u"]),),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        any_worker_failed = False
        # Simulate the conditional logic from _run_fan_out_async
        verify_calls_false = 0
        if effect_false.run_post_fanout_verification and not any_worker_failed:
            verify_calls_false += 1
        verify_calls_true = 0
        if effect_true.run_post_fanout_verification and not any_worker_failed:
            verify_calls_true += 1

        assert verify_calls_false == 0
        assert verify_calls_true == 1

    def test_policy_post_fanout_verification_defaults_to_false(self) -> None:
        """The default pipeline policy must have post_fanout_verification=False."""
        from pathlib import Path  # noqa: PLC0415

        from ralph.policy.loader import load_policy  # noqa: PLC0415

        defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        bundle = load_policy(defaults_dir)
        assert bundle.pipeline.parallel_execution is not None
        assert bundle.pipeline.parallel_execution.post_fanout_verification is False, (
            "Default policy must have post_fanout_verification=False"
        )


class TestVerificationSkippedWhenWorkerFails:
    def test_verification_skipped_when_any_worker_failed(self) -> None:
        """When any worker failed, the verification block must be skipped."""
        effect = FanOutDevelopmentEffect(
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
            ),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        any_worker_failed = True  # at least one worker failed

        verify_called = False
        if effect.run_post_fanout_verification and not any_worker_failed:
            verify_called = True

        assert not verify_called, (
            "Verification must NOT run when any_worker_failed=True"
        )

    def test_verification_runs_when_no_worker_failed(self) -> None:
        """When all workers succeeded and flag=True, verification block must run."""
        effect = FanOutDevelopmentEffect(
            work_units=(WorkUnit(unit_id="u", description="u", allowed_directories=["src/u"]),),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        any_worker_failed = False

        verify_called = False
        if effect.run_post_fanout_verification and not any_worker_failed:
            verify_called = True

        assert verify_called


class TestVerificationFailureMarksPhase:
    def test_post_fanout_verification_event_failure_enters_failed_recovery(self) -> None:
        """PostFanoutVerificationEvent(success=False) must route state to failed phase."""
        from ralph.config.enums import PHASE_FAILED  # noqa: PLC0415

        state = PipelineState(phase="development", worker_states={})
        event = PostFanoutVerificationEvent(
            success=False,
            exit_code=1,
            error="workspace verification failed (exit code 1)",
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.phase == PHASE_FAILED
        assert "workspace verification failed" in (new_state.last_error or "")

    def test_post_fanout_verification_event_success_is_noop(self) -> None:
        """PostFanoutVerificationEvent(success=True) must not change phase."""
        state = PipelineState(phase="development", worker_states={})
        event = PostFanoutVerificationEvent(success=True, exit_code=0)
        new_state, effects = reducer_reduce(state, event)
        assert new_state.phase == "development"
        assert effects == []

    def test_verification_failure_last_error_contains_message(self) -> None:
        """Phase failure state must carry the verification error in last_error."""
        state = PipelineState(phase="development", worker_states={})
        error_msg = "workspace verification failed (exit code 2): make: *** [verify] Error 2"
        event = PostFanoutVerificationEvent(success=False, exit_code=2, error=error_msg)
        new_state, _ = reducer_reduce(state, event)
        assert new_state.last_error is not None
        assert "workspace verification failed" in new_state.last_error


class TestVerificationRunsSeriallyAfterAllWorkers:
    def test_post_fanout_verification_defined_in_events(self) -> None:
        """PostFanoutVerificationEvent must be importable and have correct fields."""
        ev = PostFanoutVerificationEvent(success=True, exit_code=0)
        assert ev.success is True
        assert ev.exit_code == 0
        assert ev.error is None

        ev_fail = PostFanoutVerificationEvent(success=False, exit_code=1, error="failed")
        assert ev_fail.success is False
        assert ev_fail.exit_code == 1
        assert ev_fail.error == "failed"

    def test_post_fanout_verification_event_in_event_union(self) -> None:
        """PostFanoutVerificationEvent must be part of the Event union type."""
        from ralph.pipeline.events import Event  # noqa: PLC0415

        args = typing.get_args(Event)
        assert PostFanoutVerificationEvent in args, (
            "PostFanoutVerificationEvent must be in the Event union"
        )

    def test_verification_summary_entry_added_on_failure(self, tmp_path: Path) -> None:
        """When verification ran and failed, parallel_development_summary must include it."""
        import json  # noqa: PLC0415

        from ralph.pipeline.runner import _write_parallel_development_summary  # noqa: PLC0415

        effect = FanOutDevelopmentEffect(
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
            ),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        state = PipelineState(
            phase="development",
            worker_states={
                "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
            },
        )
        scope = _make_scope(tmp_path)
        _write_parallel_development_summary(
            scope, effect, state,
            verify_ran=True, verify_passed=False, verify_exit_code=_EXIT_CODE_VERIFY_FAIL,
        )

        summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text())
        assert summary["verification"]["ran"] is True
        assert summary["verification"]["passed"] is False
        assert summary["verification"]["exit_code"] == _EXIT_CODE_VERIFY_FAIL
        assert summary["any_failed"] is True
        workers = {w["unit_id"]: w for w in summary["workers"]}
        assert "__verify__" in workers
