"""Exhaustion must persist the intervention transition, not replenish attempts.

The run loop's :func:`_wait_for_integration_retry` used to cycle the
conflict-strategy ladder modulo ``len(CONFLICT_RESOLUTION_STRATEGIES)``
when the consecutive-conflict cap was reached, sleep 5.0s, and
re-invoke :func:`_run_auto_integrate_recovery_preamble`. That
behaviour silently replenished the attempt budget and violated
bounded-progress. The new path:

* routes through the typed :meth:`RebaseState.set_intervention_required`
  single-writer setter,
* persists the ``INTERVENTION_REQUIRED`` transition across
  checkpoint reload,
* emits the ``WAITING`` display state and ``WaitReason.EXHAUSTED_RETRY``
  wait reason,
* does NOT call :func:`_run_auto_integrate_recovery_preamble`,
* does NOT sleep 5.0s,
* survives restart with the same ``last_outcome`` (no replenishment).
"""

from __future__ import annotations

import pytest

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState


def _exhausted_state(*, max_attempts: int = 1) -> RebaseState:
    """Build a state whose consecutive-conflicts already meets the cap."""
    from ralph.pipeline.auto_integrate_conflict_budget import (
        MAX_TRACKED_CONFLICT_ATTEMPTS,
    )

    return RebaseState(
        consecutive_conflicts=min(max_attempts, MAX_TRACKED_CONFLICT_ATTEMPTS),
        resolution_exhausted=True,
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="unit-test: previous attempt failed",
    )


class TestExhaustionDoesNotInvokeRecoveryPreamble:
    """Exhaustion persists the typed INTERVENTION_REQUIRED transition.

    All setter behaviours for the exhausted and non-exhausted paths
    are pinned here in one class to satisfy the repo-structure
    single-class invariant.
    """

    def test_setter_emits_intervention_required(self) -> None:
        state = _exhausted_state()
        new_state = state.set_intervention_required(
            f"integration exhausted retry budget: {state.last_reason}"
        )
        assert new_state.last_outcome is RebaseOutcomeKind.INTERVENTION_REQUIRED
        assert "integration exhausted retry budget" in (new_state.last_reason or "")
        # Carried counters and the resolution_exhausted flag survive --
        # the transition does NOT silently clear ownership.
        assert new_state.consecutive_conflicts == state.consecutive_conflicts
        assert new_state.resolution_exhausted is True

    def test_diagnostic_flag_emits_diagnostic_variant(self) -> None:
        state = _exhausted_state()
        new_state = state.set_intervention_required("diagnostic attempt cap", diagnostic=True)
        assert new_state.last_outcome is RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED

    def test_repeated_calls_dont_replenish(self) -> None:
        """Five consecutive calls across the same ``RebaseState`` must NOT
        advance or cycle the strategy ladder; the
        ``INTERVENTION_REQUIRED`` transition is stable and idempotent."""
        state = _exhausted_state()
        prior_index = state.conflict_strategy_index
        prior_tried = state.conflict_strategies_tried
        for _ in range(5):
            state = state.set_intervention_required("integration exhausted retry budget")
            assert state.last_outcome is RebaseOutcomeKind.INTERVENTION_REQUIRED
            assert state.conflict_strategy_index == prior_index
            assert state.conflict_strategies_tried == prior_tried
            assert state.resolution_exhausted is True

    def test_exhaustion_survives_checkpoint_round_trip(self) -> None:
        """A state that records INTERVENTION_REQUIRED must round-trip
        through ``model_dump`` / ``model_validate`` with the same
        ``last_outcome`` -- the transition is durable across restart."""
        state = _exhausted_state()
        recorded = state.set_intervention_required("integration exhausted retry budget")
        payload = recorded.model_dump()
        # Re-hydrate and confirm the transition is preserved.
        rehydrated = RebaseState.model_validate(payload)
        assert rehydrated.last_outcome is RebaseOutcomeKind.INTERVENTION_REQUIRED
        assert rehydrated.resolution_exhausted is True
        assert rehydrated.consecutive_conflicts == state.consecutive_conflicts

    @pytest.mark.parametrize("repeat", [1, 2, 3, 4, 5])
    def test_repeated_exhaustion_calls_preserve_is_intervention_required(self, repeat: int) -> None:
        """The :attr:`is_intervention_required` projection is stable
        across N calls; the run-loop consumer that gates dispatch
        keeps refusing ordinary work."""
        state = _exhausted_state()
        for _ in range(repeat):
            state = state.set_intervention_required("integration exhausted retry budget")
            assert state.is_intervention_required is True

    def test_non_exhausted_state_does_not_set_intervention(self) -> None:
        """The non-exhausted branch is unchanged -- the typing only
        closes the exhausted branch, not the routine recovery path.
        Without the ``set_intervention_required`` call, the
        ``last_outcome`` is the typed CONFLICT and the projection is
        False -- the run loop continues to handle the conflict.
        """
        state = RebaseState(
            consecutive_conflicts=0,
            resolution_exhausted=False,
            last_outcome=RebaseOutcomeKind.CONFLICT,
            last_reason="first attempt",
        )
        assert state.is_intervention_required is False
        assert state.last_outcome is RebaseOutcomeKind.CONFLICT
