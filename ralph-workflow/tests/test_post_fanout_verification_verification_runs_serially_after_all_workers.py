"""Tests for serialized post-fanout workspace verification."""

from __future__ import annotations

import json
import typing

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import Event, PostFanoutVerificationEvent
from ralph.pipeline.fan_out import VerificationResult, write_parallel_development_summary
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope

if typing.TYPE_CHECKING:
    from pathlib import Path


def _minimal_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure=None,
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


_EXIT_CODE_VERIFY_FAIL = 2


def _make_scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))


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

        args = typing.get_args(Event)
        assert PostFanoutVerificationEvent in args, (
            "PostFanoutVerificationEvent must be in the Event union"
        )

    def test_verification_summary_entry_added_on_failure(self, tmp_path: Path) -> None:
        """When verification ran and failed, parallel_development_summary must include it."""


        effect = FanOutEffect(
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
        write_parallel_development_summary(
            scope,
            effect,
            state,
            VerificationResult(ran=True, passed=False, exit_code=_EXIT_CODE_VERIFY_FAIL),
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
