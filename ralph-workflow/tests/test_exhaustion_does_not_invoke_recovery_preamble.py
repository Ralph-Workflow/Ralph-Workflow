from __future__ import annotations

import pytest

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState


def _exhausted_state(*, max_attempts: int = 1) -> RebaseState:
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
    def test_setter_emits_ladder_escalation(self) -> None:
        state = _exhausted_state()
        new_state = state.set_ladder_escalation(
            f"integration exhausted retry budget: {state.last_reason}"
        )
        assert new_state.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
        assert "integration exhausted retry budget" in (new_state.last_reason or "")
        assert new_state.consecutive_conflicts == state.consecutive_conflicts
        assert new_state.resolution_exhausted is True

    def test_legacy_parse_values_remain_readable(self) -> None:
        legacy = RebaseState(last_action="intervention_required")
        assert legacy.last_outcome is RebaseOutcomeKind.INTERVENTION_REQUIRED
        legacy_diag = RebaseState(last_action="diagnostic_intervention_required")
        assert legacy_diag.last_outcome is RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED

    def test_repeated_calls_dont_replenish(self) -> None:
        state = _exhausted_state()
        prior_index = state.conflict_strategy_index
        prior_tried = state.conflict_strategies_tried
        for _ in range(5):
            state = state.set_ladder_escalation("integration exhausted retry budget")
            assert state.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
            assert state.conflict_strategy_index == prior_index
            assert state.conflict_strategies_tried == prior_tried
            assert state.resolution_exhausted is True

    def test_exhaustion_survives_checkpoint_round_trip(self) -> None:
        state = _exhausted_state()
        recorded = state.set_ladder_escalation("integration exhausted retry budget")
        payload = recorded.model_dump()
        rehydrated = RebaseState.model_validate(payload)
        assert rehydrated.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
        assert rehydrated.resolution_exhausted is True
        assert rehydrated.consecutive_conflicts == state.consecutive_conflicts

    @pytest.mark.parametrize("repeat", [1, 2, 3, 4, 5])
    def test_repeated_exhaustion_calls_preserve_is_intervention_required(self, repeat: int) -> None:
        state = _exhausted_state()
        for _ in range(repeat):
            state = state.set_ladder_escalation("integration exhausted retry budget")
            assert state.is_intervention_required is True

    def test_non_exhausted_state_does_not_set_intervention(self) -> None:
        state = RebaseState(
            consecutive_conflicts=0,
            resolution_exhausted=False,
            last_outcome=RebaseOutcomeKind.CONFLICT,
            last_reason="first attempt",
        )
        assert state.is_intervention_required is False
        assert state.last_outcome is RebaseOutcomeKind.CONFLICT
