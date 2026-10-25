from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ralph.pipeline._integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from tests._resolver_controller import make_resolver_controller


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
    write_record(tmp_path, record)
    saved_before = read_record(tmp_path)
    assert saved_before is not None
    assert saved_before.merge_commit_repair_attempts == 3
    assert saved_before.diagnostic_intervention_required is False

    # 2. Invoke genuine handoff_integration_diagnostic entry point with exhausted attempts
    handoff_success = handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=record,
        failure="diagnostic failure evidence",
        agents=["test-diagnostic-agent"],
        invoke=lambda *_: True,
        repair_attempt_cap=3,
    )
    assert handoff_success is False

    # Fault-injection checkpoint: confirm record was written to disk with budget spent reason
    saved_after = read_record(tmp_path)
    assert saved_after is not None
    assert saved_after.diagnostic_intervention_required is False
    assert "diagnostic repair budget spent" in (saved_after.repair_cap_reason or "")
    assert saved_after.merge_commit_repair_attempts == 3

    # 3. Simulate process restart by reloading from disk with exhausted record
    restarted_record = read_record(tmp_path)
    assert restarted_record is not None
    assert restarted_record.merge_commit_repair_attempts == 3
    assert restarted_record.diagnostic_intervention_required is False

    # Restart public recovery with the reloaded exhausted record; assert zero agent calls occur
    mock_invoke = MagicMock()
    handoff_restart = handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=restarted_record,
        failure="diagnostic failure after restart",
        agents=["test-diagnostic-agent"],
        invoke=mock_invoke,
        repair_attempt_cap=3,
    )
    assert handoff_restart is False
    mock_invoke.assert_not_called()

    # Confirm no attempt budget replenishment: attempts counter is still 3, not reset or incremented
    after_restart = read_record(tmp_path)
    assert after_restart is not None
    assert after_restart.merge_commit_repair_attempts == 3
    assert after_restart.diagnostic_intervention_required is False

    # 4. Verify reducer with clean state yields BLOCK_FOR_RECOVERY from durable record
    state = PipelineState(phase="development", rebase=RebaseState())
    policy = _policy()
    new_state, _ = reduce(
        state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=after_restart
    )
    assert new_state.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value

    # Direct authority evaluation also confirms BLOCK_FOR_RECOVERY
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=after_restart,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
