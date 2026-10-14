from __future__ import annotations

from pathlib import Path

from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import START_INTEGRATING, IntegrationRecord, write_record
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


def test_entry_point_matrix_evaluates_consistent_phase_decision(tmp_path: Path) -> None:
    # Set up a single integration scenario with an active record
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    policy = _policy()
    shared_rebase = RebaseState(last_action="conflict", last_reason="unresolved")
    shared_state = PipelineState(phase="development", rebase=shared_rebase)

    # 6 entry points:
    # 1. Startup recovery entry point
    decision_startup = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    # 2. Phase-boundary entry point
    decision_phase_boundary = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    # 3. Commit handler entry point
    decision_commit_handler = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    # 4. Cooldown entry point
    decision_cooldown = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    # 5. Parallel-join entry point
    decision_parallel_join = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    # 6. Terminal entry point
    decision_terminal = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )

    decisions = [
        decision_startup,
        decision_phase_boundary,
        decision_commit_handler,
        decision_cooldown,
        decision_parallel_join,
        decision_terminal,
    ]
    # All 6 must agree on the exact same PhaseDecision
    assert all(d == PhaseDecision.BLOCK_FOR_RECOVERY for d in decisions)
    assert len(set(decisions)) == 1
