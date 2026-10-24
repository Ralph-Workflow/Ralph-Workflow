"""Single-owner integration decision tests.

The owner adapter (:mod:`ralph.pipeline.integration_owner`) is the ONE
place an integration decision is produced. These tests pin the three
contracts that make it safe for every other seam to delegate to it:

* **The projection is total.** Every one of the nine
  :class:`~ralph.pipeline.integration_decision.NextAction` members maps
  to exactly one
  :class:`~ralph.pipeline._integration_action.IntegrationAction`; a
  member without a mapping raises ``ValueError`` instead of defaulting
  to a permissive action.
* **The charge boundary is exact.** A mutation-class decision is
  charged to the durable attempt ledger exactly once; a
  non-mutation-class decision spends no escalation budget.
* **The reason comes from the current observation.** The reason is
  rendered from the seven-dimension observation that produced the
  decision, never from a stored message.

No real subprocess, no real ``time.sleep`` and no real filesystem
outside the per-test ``tmp_path`` fixture: the observation is injected
and the durable ledger is written by the production code under
``tmp_path``. This is the ``audit_test_policy`` contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline import integration_owner
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import AttemptState, EscalationRung, NextAction
from ralph.pipeline.integration_director import IntegrationDirector
from ralph.pipeline.integration_observation import (
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

# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


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
    """Build a synthetic seven-dimension observation."""
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
    """Return the durable attempt-ledger path used by one test."""
    return tmp_path / ".agent" / "integration_attempt_ledger.json"


def _ledger_state(tmp_path: Path) -> AttemptLedgerState:
    """Read the durable ledger state back from disk."""
    return AttemptLedger(tmp_path, ledger_path=_ledger_path(tmp_path)).read()


def _signature(observation: Observation) -> str:
    """Return the durable state signature the ledger charges against."""
    return AttemptState().signature(observation)


# ---------------------------------------------------------------------------
# The projection is total.
# ---------------------------------------------------------------------------


def test_every_next_action_member_maps_to_exactly_one_integration_action() -> None:
    """All nine decision members project onto a closed integration action."""
    assert len(NextAction) == 9
    for member in NextAction:
        projected = project_integration_action(member)
        assert isinstance(projected, IntegrationAction)
    assert set(NEXT_ACTION_TO_INTEGRATION_ACTION) == set(NextAction)


def test_unmapped_next_action_member_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member without a mapping fails closed instead of defaulting.

    Simulates a future ``NextAction`` member that the projection table
    does not cover yet: the projection must raise rather than pick a
    permissive action for it.
    """
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
    """The chargeable set is exactly the mutation-class decisions.

    Charge rule: a decision whose action asks the boundary to change
    integration state is charged to the durable ledger; the two
    wait-shaped decisions are not.
    """
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


# ---------------------------------------------------------------------------
# The projection reaches the integration vocabulary.
# ---------------------------------------------------------------------------


def test_landed_work_projects_to_land_target(tmp_path: Path) -> None:
    """Contained feature + clean worktree lands the target."""
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.LAND_TARGET


def test_conflicted_worktree_projects_to_resolve_active_operation(tmp_path: Path) -> None:
    """An unmerged worktree dispatches the resolver for the active operation."""
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(worktree=WorktreeState.UNMERGED),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION


def test_dirty_worktree_projects_to_continue_git_operation(tmp_path: Path) -> None:
    """Uncommitted worktree changes continue the git operation."""
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(worktree=WorktreeState.TRACKED_CHANGES),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.CONTINUE_GIT_OPERATION


def test_repairable_record_projects_to_resolve_active_operation(tmp_path: Path) -> None:
    """A stale durable record on a not-yet-landed tree drives the repair rung."""
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
    """A target that moved past the feature reintegrates against it."""
    action, _reason = decide_integration(
        tmp_path,
        observation=_observation(target_vs_feature=TargetVsFeature.BEHIND),
        ledger_path=_ledger_path(tmp_path),
    )
    assert action is IntegrationAction.REINTEGRATE_AGAINST_TARGET


# ---------------------------------------------------------------------------
# The charge boundary.
# ---------------------------------------------------------------------------


def test_mutation_class_decision_is_charged_to_the_ledger_exactly_once(tmp_path: Path) -> None:
    """One mutation-class decision persists exactly one escalation charge.

    Charge rule: a mutation-class decision (an action that asks the
    boundary to change integration state) is charged to the durable
    ledger exactly once - one repeat count for the observation's
    ``(signature, action)`` key. Two charges for one decision would
    burn the escalation budget twice for a single attempt.
    """
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
    """A pure environment wait records no repeat count and no rung advance.

    Charge rule: a non-mutation-class decision spends no escalation
    budget. A pure environment wait is charged against the SEPARATE,
    finite unknown-attempt budget - that budget is what makes the wait
    finite instead of endless - but it never records a repeat count and
    never advances the ladder rung.
    """
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
    """A decision with an exhausted unknown budget writes no charge at all.

    The escalation ladder is exhausted, so the boundary stops spending
    budget: no repeat count, no rung advance, no unknown-attempt
    charge.
    """
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
    """The charge is persisted: a fresh ledger read sees the attempt."""
    ledger_path = _ledger_path(tmp_path)
    observation = _observation(worktree=WorktreeState.UNMERGED)
    decide_integration(tmp_path, observation=observation, ledger_path=ledger_path)

    reloaded = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    assert reloaded.repeat_counts == {(_signature(observation), "dispatch_resolver"): 1}


# ---------------------------------------------------------------------------
# The reason comes from the current observation.
# ---------------------------------------------------------------------------


def test_reason_is_derived_from_the_current_observation(tmp_path: Path) -> None:
    """Two different observations produce two different reasons."""
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
    """The reason is rendered from the observation, not from the record."""
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
    """A caller holding a record the observation does not see fails closed.

    The caller holds a durable record while the observation proves there
    is none: the caller is holding a stale view. The owner projects that
    onto the repairable record state instead of deciding as if no
    integration obligation existed.
    """
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


# ---------------------------------------------------------------------------
# The owner observes through the existing machinery.
# ---------------------------------------------------------------------------


def test_owner_observes_through_the_director_machinery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without an injected observation the owner observes via the director."""
    sentinel = _observation(worktree=WorktreeState.UNMERGED)

    def _fake_observe(self: IntegrationDirector) -> Observation:
        return sentinel

    monkeypatch.setattr(IntegrationDirector, "observe", _fake_observe)

    action, reason = decide_integration(tmp_path, ledger_path=_ledger_path(tmp_path))

    assert action is IntegrationAction.RESOLVE_ACTIVE_OPERATION
    assert "worktree=unmerged" in reason


def test_verdict_exposes_the_charged_ledger_state(tmp_path: Path) -> None:
    """The verdict carries the observation and the charged ledger state.

    This is the seam the director delegates to: it never has to re-read
    the ledger to learn what the decision cost.
    """
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
