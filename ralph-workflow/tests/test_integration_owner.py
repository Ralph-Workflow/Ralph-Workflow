from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import integration_owner
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import (
    AttemptState,
    CleanupLadderRung,
    DirtAttribution,
    EscalationRung,
    NextAction,
)
from ralph.pipeline.integration_director import IntegrationDirector
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from ralph.pipeline.integration_owner import (
    MUTATION_CLASS_ACTIONS,
    NEXT_ACTION_TO_INTEGRATION_ACTION,
    decide_integration,
    decide_integration_verdict,
    is_chargeable,
    project_integration_action,
)


def _observation(
    *,
    target_vs_feature: TargetVsFeature = TargetVsFeature.EQUAL,
    target_vs_remote: TargetVsRemote = TargetVsRemote.EQUAL,
    worktree: WorktreeState = WorktreeState.CLEAN,
    head: HeadState = HeadState.ATTACHED,
    in_progress_kind: InProgressKind = InProgressKind.NONE,
    in_progress_ownership: InProgressOwnership = InProgressOwnership.OWNED,
    record: IntegrationRecordState = IntegrationRecordState.NONE,
    agent_active: bool = False,
) -> Observation:
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=worktree,
        head=head,
        in_progress_kind=in_progress_kind,
        in_progress_ownership=in_progress_ownership,
        record=record,
        agent_active=agent_active,
    )


def _ledger_path(tmp_path: Path) -> Path:
    return tmp_path / ".agent" / "integration_attempt_ledger.json"


def _ledger_state(tmp_path: Path) -> AttemptLedgerState:
    return AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path)).read()


def _signature(observation: Observation) -> str:
    return AttemptState().signature(observation)


def test_every_next_action_member_maps_to_exactly_one_integration_action() -> None:
    assert len(NextAction) >= 9
    for member in NextAction:
        projected = project_integration_action(member)
        assert isinstance(projected, IntegrationAction)
    assert set(NEXT_ACTION_TO_INTEGRATION_ACTION) == set(NextAction)


def test_unmapped_next_action_member_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    without_land = {
        member: action
        for member, action in integration_owner.NEXT_ACTION_TO_INTEGRATION_ACTION.items()
        if member is not NextAction.LAND
    }
    monkeypatch.setattr(
        integration_owner,
        "NEXT_ACTION_TO_INTEGRATION_ACTION",
        without_land,
    )
    with pytest.raises(ValueError):
        project_integration_action(NextAction.LAND)


def test_only_mutation_class_decisions_are_chargeable() -> None:
    chargeable = (
        NextAction.COMMIT,
        NextAction.LAND,
        NextAction.REINTEGRATE,
        NextAction.RESUME_OWNED_OPERATION,
        NextAction.REPAIR,
        NextAction.DISPATCH_RESOLVER,
        NextAction.REBUILD_ON_TARGET,
    )
    for member in chargeable:
        assert is_chargeable(member) is True
        assert member in MUTATION_CLASS_ACTIONS
    for member in (NextAction.CONTINUE_WITH_INTEGRATION_PENDING, NextAction.WAIT_FOR_ENVIRONMENT):
        assert is_chargeable(member) is False
        assert member not in MUTATION_CLASS_ACTIONS


def test_landed_work_projects_to_land_target(tmp_path: Path) -> None:
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.LAND_TARGET


def test_conflicted_worktree_projects_to_resolve_active_operation(tmp_path: Path) -> None:
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(worktree=WorktreeState.UNMERGED),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION


def test_dirty_worktree_projects_to_continue_git_operation(tmp_path: Path) -> None:
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(worktree=WorktreeState.TRACKED_CHANGES),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.CONTINUE_GIT_OPERATION


def test_repairable_record_projects_to_resolve_active_operation(tmp_path: Path) -> None:
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(
            target_vs_feature=TargetVsFeature.BEHIND,
            record=IntegrationRecordState.STALE,
        ),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION


def test_target_moved_projects_to_reintegrate_against_target(tmp_path: Path) -> None:
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(target_vs_feature=TargetVsFeature.BEHIND),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.REINTEGRATE_AGAINST_TARGET


def test_mutation_class_decision_is_charged_to_the_ledger_exactly_once(tmp_path: Path) -> None:
    observation = _observation(worktree=WorktreeState.UNMERGED)
    action, _reason = decide_integration(
        tmp_path,
        observation=observation,
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION

    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {(_signature(observation), "dispatch_resolver"): 1}
    assert state.rung is EscalationRung.REPAIR


def test_pure_wait_spends_no_escalation_budget(tmp_path: Path) -> None:
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(worktree=WorktreeState.UNKNOWN),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {}
    assert state.rung is EscalationRung.REPAIR
    assert state.unknown_attempts == 1


def test_exhausted_unknown_budget_decision_spends_no_budget_at_all(tmp_path: Path) -> None:
    ledger_path = _ledger_path(tmp_path)
    seed = AttemptLedger(tmp_path, ledger_path=ledger_path, unknown_budget=1)
    seed.charge("seed-signature", "wait", is_unknown=True)

    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(worktree=WorktreeState.UNKNOWN),
        ledger_path=ledger_path,
    )
    assert action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {}
    assert state.rung is EscalationRung.REPAIR
    assert state.unknown_attempts == 1


def test_the_charge_is_durable_before_the_caller_acts(tmp_path: Path) -> None:
    ledger_path = _ledger_path(tmp_path)
    observation = _observation(worktree=WorktreeState.UNMERGED)
    decide_integration(tmp_path, observation=observation, ledger_path=ledger_path)

    reloaded = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    assert reloaded.repeat_counts == {(_signature(observation), "dispatch_resolver"): 1}


def test_reason_is_derived_from_the_current_observation(tmp_path: Path) -> None:
    landed = _observation(worktree=WorktreeState.CLEAN)
    conflicted = _observation(worktree=WorktreeState.UNMERGED)

    _, landed_reason = decide_integration(
        tmp_path,
        observation=landed,
        ledger_path=_ledger_path(tmp_path),
    )
    _, conflicted_reason = decide_integration(
        tmp_path,
        observation=conflicted,
        ledger_path=_ledger_path(tmp_path),
    )

    assert landed_reason != conflicted_reason
    assert "worktree=clean" in landed_reason
    assert "worktree=unmerged" in conflicted_reason


def test_reason_never_echoes_a_stored_durable_message(tmp_path: Path) -> None:
    marker = "durable-record-marker"
    record = IntegrationRecord(
        phase="integrating",
        target=marker,
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
    )
    _, reason = decide_integration(
        tmp_path,
        record=record,
        observation=_observation(),
        ledger_path=_ledger_path(tmp_path),
    )
    assert marker not in reason
    assert "record=stale" in reason


def test_caller_record_the_observation_does_not_see_fails_closed(tmp_path: Path) -> None:
    observation = _observation(target_vs_feature=TargetVsFeature.BEHIND)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="0" * 40,
    )

    without_record, _ = decide_integration(
        tmp_path,
        observation=observation,
        ledger_path=_ledger_path(tmp_path),
    )
    with_record, reason = decide_integration(
        tmp_path,
        record=record,
        observation=observation,
        ledger_path=_ledger_path(tmp_path),
    )

    assert without_record is IntegrationAction.REINTEGRATE_AGAINST_TARGET
    assert with_record is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert "record=stale" in reason


def test_owner_observes_through_the_director_machinery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sentinel = _observation(worktree=WorktreeState.UNMERGED)

    def _fake_observe(self: IntegrationDirector) -> Observation:
        return sentinel

    monkeypatch.setattr(IntegrationDirector, "observe", _fake_observe)

    action, reason = decide_integration(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert "worktree=unmerged" in reason


def test_verdict_exposes_the_charged_ledger_state(tmp_path: Path) -> None:
    verdict = decide_integration_verdict(
        tmp_path,
        observation=_observation(worktree=WorktreeState.UNMERGED),
        ledger_path=_ledger_path(tmp_path),
    )

    assert verdict.action is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert verdict.escalation_charged is True
    assert verdict.observation.worktree is WorktreeState.UNMERGED
    assert verdict.attempts.repeat_counts == {
        (_signature(verdict.observation), "dispatch_resolver"): 1
    }


def test_dirty_tree_verdict_carries_the_cleanup_ladder_rung(tmp_path: Path) -> None:
    verdict = decide_integration_verdict(
        tmp_path,
        observation=_observation(worktree=WorktreeState.TRACKED_CHANGES),
        ledger_path=_ledger_path(tmp_path),
    )

    assert verdict.ladder_rung is CleanupLadderRung.NORMAL_CLEANUP
    assert verdict.action is IntegrationAction.CONTINUE_GIT_OPERATION
    assert verdict.dirt_shape is DirtShape.UNSTAGED_MODIFICATION
    assert verdict.dirt_attribution is DirtAttribution.UNOWNED
    assert verdict.escalation_charged is True

    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {(_signature(verdict.observation), "save_and_restore"): 1}


def test_repeated_unchanged_dirty_observation_escalates_the_rung(
    tmp_path: Path,
) -> None:
    ledger_path = _ledger_path(tmp_path)
    observation = _observation(worktree=WorktreeState.TRACKED_CHANGES)

    first = decide_integration_verdict(tmp_path, observation=observation, ledger_path=ledger_path)
    second = decide_integration_verdict(tmp_path, observation=observation, ledger_path=ledger_path)
    third = decide_integration_verdict(tmp_path, observation=observation, ledger_path=ledger_path)

    assert first.ladder_rung is CleanupLadderRung.NORMAL_CLEANUP
    assert second.ladder_rung is CleanupLadderRung.NORMAL_CLEANUP
    assert third.ladder_rung is CleanupLadderRung.ALTERNATE_PRESERVATION
    assert third.escalation_charged is True


def test_clean_tree_verdict_carries_no_ladder_rung(tmp_path: Path) -> None:
    verdict = decide_integration_verdict(
        tmp_path,
        observation=_observation(),
        ledger_path=_ledger_path(tmp_path),
    )

    assert verdict.ladder_rung is None
    assert verdict.dirt_shape is DirtShape.NONE
    assert verdict.dirt_attribution is DirtAttribution.UNOWNED


def test_unknown_reading_verdict_carries_no_ladder_rung(tmp_path: Path) -> None:
    verdict = decide_integration_verdict(
        tmp_path,
        observation=_observation(worktree=WorktreeState.UNKNOWN),
        ledger_path=_ledger_path(tmp_path),
    )

    assert verdict.ladder_rung is None
    assert verdict.action is IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    assert verdict.escalation_charged is False


def test_explicit_dirt_refinement_overrides_internal_classification(
    tmp_path: Path,
) -> None:
    verdict = decide_integration_verdict(
        tmp_path,
        observation=_observation(worktree=WorktreeState.TRACKED_CHANGES),
        ledger_path=_ledger_path(tmp_path),
        dirt_shape=DirtShape.STAGED_ONLY,
        dirt_attribution=DirtAttribution.PHASE_OWNED,
    )

    assert verdict.ladder_rung is None
    assert verdict.dirt_shape is DirtShape.STAGED_ONLY
    assert verdict.dirt_attribution is DirtAttribution.PHASE_OWNED
    state = _ledger_state(tmp_path)
    assert state.repeat_counts == {(_signature(verdict.observation), "commit_via_commit_phase"): 1}
