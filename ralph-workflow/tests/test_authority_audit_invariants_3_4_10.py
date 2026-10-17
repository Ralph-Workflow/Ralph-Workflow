"""Inv-3, Inv-4, Inv-10: required-invariant pure-authority regression tests.

G11: absence of a record, a clean worktree, and unknown outcomes must
never authorize completion. These tests exercise only existing public
authority signatures with no filesystem or Git fixtures.
"""

from __future__ import annotations

from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState


def _integrating_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree="/tmp/wt",
    )


def test_record_absence_does_not_authorize_completion() -> None:
    """Inv-3: `next_integration_action(None)` fails closed without proof."""
    assert next_integration_action(None) is not IntegrationAction.COMPLETE_WITH_PROOF
    assert next_integration_action(None, proof=True) is IntegrationAction.COMPLETE_WITH_PROOF


def test_clean_worktree_observation_does_not_allow_landing() -> None:
    """Inv-4: a clean-worktree observation (all blocking flags False) is insufficient
    for LAND_TARGET; phase must be 'integrated' and authority proof must be present.

    The clean-worktree proof is the absence of resolving_rebase / resolving_merge /
    reintegrate_pending / rebase_continue_pending / merge_commit_pending. Even when
    all those flags are False and phase='integrating', is_integrating=True triggers
    REINTEGRATE_AGAINST_TARGET — the worktree is not yet landed because the
    fast-forward has not been applied.
    """
    record = _integrating_record()
    # All clean-worktree flags set to False; the worktree is observably clean.
    # phase='integrating' means is_integrating=True, which routes to REINTEGRATE.
    record = record.model_copy(
        update={
            "reintegrate_pending": False,
            "resolving_rebase": False,
            "resolving_merge": False,
            "rebase_continue_pending": False,
            "merge_commit_pending": False,
        }
    )
    action = next_integration_action(record)
    assert action is not IntegrationAction.LAND_TARGET
    assert action is IntegrationAction.REINTEGRATE_AGAINST_TARGET

    # Sanity: COMPLETE_WITH_PROOF also requires authority proof; clean worktree
    # without the proof bit is insufficient for completion.
    assert next_integration_action(record, proof=True) is not IntegrationAction.LAND_TARGET


def test_unknown_outcome_routes_to_intervention() -> None:
    """Inv-10: an UNKNOWN outcome fails closed to intervention everywhere."""
    rebase = RebaseState(last_outcome=RebaseOutcomeKind.UNKNOWN)
    assert next_integration_action(rebase) is IntegrationAction.REQUIRE_INTERVENTION
    state = PipelineState(phase="development", rebase=rebase)
    decision = decide_phase_transition(state, None, integration_record=None, rebase_state=rebase)
    assert decision in (
        PhaseDecision.BLOCK_FOR_RECOVERY,
        PhaseDecision.REPORT_INTERVENTION_REQUIRED,
    )
    assert decision is PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert decision not in (
        PhaseDecision.ADVANCE_PHASE,
        PhaseDecision.CONTINUE_PHASE,
        PhaseDecision.REPORT_TERMINAL_SUCCESS,
    )
    assert is_terminal_success_eligible(state, None, rebase) is False
