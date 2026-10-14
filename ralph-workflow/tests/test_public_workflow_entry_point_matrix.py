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
    shared_rebase = RebaseState()
    shared_state = PipelineState(phase="development", rebase=shared_rebase)

    from ralph.pipeline._terminal_authority import is_terminal_success_eligible
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.reducer import reduce

    # 1. Startup recovery entry point
    decision_startup = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    # 2. Phase-boundary entry point via reducer
    st_phase_boundary, _ = reduce(
        shared_state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=record
    )
    assert st_phase_boundary.rebase.last_decision is not None
    decision_phase_boundary = PhaseDecision(st_phase_boundary.rebase.last_decision)
    # 3. Commit handler entry point via reducer
    st_commit, _ = reduce(
        shared_state, PipelineEvent.COMMIT_SUCCESS, policy, integration_record=record
    )
    assert st_commit.rebase.last_decision is not None
    decision_commit_handler = PhaseDecision(st_commit.rebase.last_decision)
    # 4. Cooldown entry point via reducer
    st_cooldown, _ = reduce(
        shared_state, PipelineEvent.PHASE_LOOPBACK, policy, integration_record=record
    )
    assert st_cooldown.rebase.last_decision is not None
    decision_cooldown = PhaseDecision(st_cooldown.rebase.last_decision)
    # 5. Parallel-join entry point via reducer
    st_join, _ = reduce(
        shared_state, PipelineEvent.ALL_WORKERS_COMPLETE, policy, integration_record=record
    )
    assert st_join.rebase.last_decision is not None
    decision_parallel_join = PhaseDecision(st_join.rebase.last_decision)
    # 6. Terminal entry point
    terminal_state = PipelineState(phase="complete", rebase=shared_rebase)
    decision_terminal = decide_phase_transition(
        terminal_state, policy, integration_record=record, rebase_state=shared_rebase
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

    # Terminal success eligibility is rejected while active integration record is present
    assert is_terminal_success_eligible(terminal_state, record, shared_rebase) is False
