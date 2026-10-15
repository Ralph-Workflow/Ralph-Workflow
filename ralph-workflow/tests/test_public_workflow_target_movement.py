from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ralph.pipeline._owned_landing_reintegration import reintegrate_owned_landing
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
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
    # 1. Start integrating with ownership
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    # 2. Target moved: invoke genuine reintegrate_owned_landing workflow entry point
    with (
        patch(
            "ralph.pipeline._owned_landing_reintegration.rebase_in_progress_at", return_value=False
        ),
        patch("ralph.pipeline._owned_landing_reintegration.merge_state", return_value=None),
        patch("ralph.pipeline._owned_landing_reintegration.get_head_sha", return_value="abc111"),
        patch("ralph.pipeline._owned_landing_reintegration.branch_sha", return_value="def999"),
        patch("ralph.pipeline._owned_landing_reintegration.is_repo_clean", return_value=False),
    ):
        outcome = reintegrate_owned_landing(tmp_path, record, None, None, None)

    # 3. Assert durable record is on disk with reintegrate_pending=True and owning_worktree preserved
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.reintegrate_pending is True
    assert on_disk.owning_worktree == str(tmp_path)
    assert on_disk.pre_feature_sha == "abc111"
    assert outcome.recovery_record_retained is True

    # 4. Assert reduce with integration_record preserves development phase and returns BLOCK_FOR_RECOVERY
    state = PipelineState(phase="development", rebase=outcome)
    policy = _policy()
    new_state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=on_disk)
    assert new_state.phase == "development"
    assert new_state.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value

    # Direct authority evaluation also confirms blocking decision
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=on_disk,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
