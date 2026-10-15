"""Crash/restart matrix verifying recovery across durable pipeline transitions.

Injects crashes before and after the 5 core integration transitions:
1. Integration (START_INTEGRATING)
2. Continuation (MARK_PENDING_REBASE_CONTINUE)
3. Reintegration (MARK_REINTEGRATE_PENDING)
4. Landing (PROMOTE_TO_INTEGRATED)
5. Publication (Publication after landing)

Asserts each produces a known typed PhaseDecision or IntegrationAction without
replaying completed business work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    MARK_PENDING_REBASE_CONTINUE,
    MARK_REINTEGRATE_PENDING,
    PROMOTE_TO_INTEGRATED,
    START_INTEGRATING,
    IntegrationRecord,
    clear_record,
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


def _setup_integration(
    tmp_path: Path, crash_point: str
) -> tuple[IntegrationRecord | None, RebaseState, str]:
    if crash_point == "before":
        clear_record(tmp_path)
        return None, RebaseState(), "development"
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha_111",
        pre_target_sha="tgt_sha_222",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)
    return record, RebaseState(), "development"


def _setup_continuation(
    tmp_path: Path, crash_point: str
) -> tuple[IntegrationRecord | None, RebaseState, str]:
    if crash_point == "before":
        record = IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="feat_sha_111",
            pre_target_sha="tgt_sha_222",
            resolving_rebase=True,
            owning_worktree=str(tmp_path),
        ).with_transition(START_INTEGRATING)
        write_record(tmp_path, record)
        return record, RebaseState(), "development"
    base_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha_111",
        pre_target_sha="tgt_sha_222",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    record = base_record.with_transition(
        MARK_PENDING_REBASE_CONTINUE,
        rebase_continue_pending=True,
        rebase_continue_head="continue_sha",
    )
    write_record(tmp_path, record)
    return record, RebaseState(), "development"


def _setup_reintegration(
    tmp_path: Path, crash_point: str
) -> tuple[IntegrationRecord | None, RebaseState, str]:
    base_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha_111",
        pre_target_sha="tgt_sha_222",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    if crash_point == "before":
        write_record(tmp_path, base_record)
        return base_record, RebaseState(), "development"
    record = base_record.with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
    )
    write_record(tmp_path, record)
    return record, RebaseState(), "development"


def _setup_landing(
    tmp_path: Path, crash_point: str
) -> tuple[IntegrationRecord | None, RebaseState, str]:
    base_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha_111",
        pre_target_sha="tgt_sha_222",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    if crash_point == "before":
        write_record(tmp_path, base_record)
        return base_record, RebaseState(), "development"
    record = base_record.with_transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="feat_sha_integrated",
    )
    write_record(tmp_path, record)
    return record, RebaseState(), "development"


def _setup_publication(
    tmp_path: Path, crash_point: str
) -> tuple[IntegrationRecord | None, RebaseState, str]:
    clear_record(tmp_path)
    if crash_point == "before":
        rebase = RebaseState(
            last_outcome=RebaseOutcomeKind.REBASED,
            last_publication_decision="publish_now",
            fast_forwarded=True,
        )
        return None, rebase, "development"
    rebase = RebaseState(
        last_outcome=RebaseOutcomeKind.REMOTE_PUSHED,
        fast_forwarded=True,
    )
    return None, rebase, "complete"


_SCENARIO_SETUPS = {
    "integration": _setup_integration,
    "continuation": _setup_continuation,
    "reintegration": _setup_reintegration,
    "landing": _setup_landing,
    "publication": _setup_publication,
}


@pytest.mark.parametrize(
    ("transition_name", "crash_point", "expected_action", "expected_decision"),
    [
        (
            "integration",
            "before",
            IntegrationAction.COMPLETE_WITH_PROOF,
            PhaseDecision.REPORT_TERMINAL_SUCCESS,
        ),
        (
            "integration",
            "after",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "continuation",
            "before",
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "continuation",
            "after",
            IntegrationAction.CONTINUE_GIT_OPERATION,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "reintegration",
            "before",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "reintegration",
            "after",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "landing",
            "before",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "landing",
            "after",
            IntegrationAction.LAND_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "publication",
            "before",
            IntegrationAction.PUBLISH_AFTER_LANDING,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "publication",
            "after",
            IntegrationAction.COMPLETE_WITH_PROOF,
            PhaseDecision.REPORT_TERMINAL_SUCCESS,
        ),
    ],
)
def test_crash_restart_matrix_routes_authoritatively(
    tmp_path: Path,
    transition_name: str,
    crash_point: str,
    expected_action: IntegrationAction,
    expected_decision: PhaseDecision,
) -> None:
    policy = _policy()

    setup_fn = _SCENARIO_SETUPS[transition_name]
    record, rebase, current_phase = setup_fn(tmp_path, crash_point)

    # Simulate process crash and restart: reload record from disk
    restarted_record = read_record(tmp_path)

    # Assert authority outcomes without replaying completed work
    if record is not None:
        assert restarted_record is not None
        assert restarted_record.phase == record.phase
        assert restarted_record.pre_feature_sha == record.pre_feature_sha
        assert restarted_record.rebase_continue_pending == record.rebase_continue_pending
        assert restarted_record.reintegrate_pending == record.reintegrate_pending
        assert restarted_record.integrated_feature_sha == record.integrated_feature_sha
        action = next_integration_action(restarted_record, policy=policy)
    else:
        assert restarted_record is None
        action = next_integration_action(rebase, policy=policy)

    assert action == expected_action

    state = PipelineState(phase=current_phase, rebase=rebase)
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=restarted_record,
        rebase_state=rebase,
    )
    assert decision == expected_decision
