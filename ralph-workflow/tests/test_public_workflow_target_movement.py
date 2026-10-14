from __future__ import annotations

from pathlib import Path

from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    START_INTEGRATING,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def test_target_movement_preserves_ownership_and_blocks_for_recovery(tmp_path: Path) -> None:
    # 1. Start integrating
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(tmp_path),
    )
    record = record.with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    # 2. Target moved: transition to MARK_REINTEGRATE_PENDING
    updated = record.with_transition(MARK_REINTEGRATE_PENDING, reintegrate_pending=True)
    write_record(tmp_path, updated)

    # 3. Assert durable record is still on disk with reintegrate_pending=True and owning_worktree preserved
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.reintegrate_pending is True
    assert on_disk.owning_worktree == str(tmp_path)
    assert on_disk.pre_feature_sha == "abc111"

    # 4. Assert decide_phase_transition returns BLOCK_FOR_RECOVERY
    state = PipelineState(phase="development", rebase=RebaseState())
    policy = _policy()
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=on_disk,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
