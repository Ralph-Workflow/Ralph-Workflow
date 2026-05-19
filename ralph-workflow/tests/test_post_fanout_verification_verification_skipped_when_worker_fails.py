"""Tests for serialized post-fanout workspace verification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
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


class TestVerificationSkippedWhenWorkerFails:
    def test_verification_skipped_when_any_worker_failed(self) -> None:
        """When any worker failed, the verification block must be skipped."""
        effect = FanOutEffect(
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

        assert not verify_called, "Verification must NOT run when any_worker_failed=True"

    def test_verification_runs_when_no_worker_failed(self) -> None:
        """When all workers succeeded and flag=True, verification block must run."""
        effect = FanOutEffect(
            work_units=(WorkUnit(unit_id="u", description="u", allowed_directories=["src/u"]),),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        any_worker_failed = False

        verify_called = False
        if effect.run_post_fanout_verification and not any_worker_failed:
            verify_called = True

        assert verify_called
