from __future__ import annotations

from pathlib import Path

from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    RAISE_DIAGNOSTIC_INTERVENTION,
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


def test_exhaustion_restart_prevents_budget_replenishment(tmp_path: Path) -> None:
    # 1. Create a record that has reached max diagnostic repairs (3 attempts)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        merge_commit_pending=True,
        merge_commit_repair_attempts=3,
        owning_worktree=str(tmp_path),
    )
    # Fault-injection checkpoint before transition: verify state before transition
    write_record(tmp_path, record)
    saved_before = read_record(tmp_path)
    assert saved_before is not None
    assert saved_before.merge_commit_repair_attempts == 3
    assert saved_before.diagnostic_intervention_required is False

    # 2. Transition with RAISE_DIAGNOSTIC_INTERVENTION
    intervened = record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason="merge_commit_repair_attempts_exhausted",
    )
    assert intervened.diagnostic_intervention_required is True
    assert intervened.repair_cap_reason == "merge_commit_repair_attempts_exhausted"
    write_record(tmp_path, intervened)

    # Fault-injection checkpoint after transition: simulate crash and restart
    saved_after = read_record(tmp_path)
    assert saved_after is not None
    assert saved_after.diagnostic_intervention_required is True
    assert saved_after.merge_commit_repair_attempts == 3

    # 3. Simulate process restart by reloading from disk and evaluating decide_phase_transition
    restarted_record = read_record(tmp_path)
    assert restarted_record is not None
    # Confirm no attempt budget replenishment: attempts counter is still 3, not reset to 0
    assert restarted_record.merge_commit_repair_attempts == 3
    assert restarted_record.diagnostic_intervention_required is True

    # 4. Verify decide_phase_transition returns REPORT_INTERVENTION_REQUIRED
    state = PipelineState(phase="development", rebase=RebaseState())
    policy = _policy()
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=restarted_record,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED
