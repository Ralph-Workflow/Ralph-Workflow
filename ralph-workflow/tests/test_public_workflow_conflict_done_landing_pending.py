from __future__ import annotations

from pathlib import Path

from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    PROMOTE_TO_INTEGRATED,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
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
            "integrating": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="integrating",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def test_conflict_done_landing_pending_blocks_recovery_and_prevents_dev_phase(
    tmp_path: Path,
) -> None:
    # 1. Conflict resolved, promote to integrated with landing pending (integrated_feature_sha is None)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(tmp_path),
    )
    promoted = record.with_transition(PROMOTE_TO_INTEGRATED, phase="integrated")
    assert promoted.phase == "integrated"
    assert promoted.integrated_feature_sha is None
    write_record(tmp_path, promoted)

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.phase == "integrated"

    # 2. Clean pipeline state without pre-seeded decision
    state = PipelineState(
        phase="integrating",
        rebase=RebaseState(),
    )
    policy = _policy()

    # 3. decide_phase_transition returns BLOCK_FOR_RECOVERY based strictly on durable record
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=on_disk,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY

    # 4. Reducer receives integration_record, preserves the non-business phase and does not return to development
    new_state, _ = reduce(state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=on_disk)
    assert new_state.phase == "integrating"
    assert new_state.phase != "development"
    assert new_state.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value
