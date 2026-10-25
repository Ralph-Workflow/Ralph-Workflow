"""Tests for the five new typed setters on RebaseState.

These setters are the single-writer contract for the conflict-strategy
ladder, the bounded consecutive-conflict counter, the typed
``INTERVENTION_REQUIRED`` transition, and the durable
``retry_deadline`` field. The :file:`test_rebase_state_setters_owner.py`
AST scan is the structural guarantee that no other module writes those
fields through ``model_copy``/``copy_with``; this file proves the
behavioural guarantees each setter is supposed to enforce.

All setter behaviours are pinned in one top-level class to satisfy the
repo-structure single-class invariant.
"""

from __future__ import annotations

import pytest

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_TRACKED_CONFLICT_ATTEMPTS,
)
from ralph.pipeline.auto_integrate_resolution_state import (
    CONFLICT_RESOLUTION_STRATEGIES,
)
from ralph.pipeline.rebase_state import RebaseState


class TestConflictBudgetTypedSetters:
    """Behavioural guarantees for the five typed setters on ``RebaseState``."""

    # ----- set_retry_deadline -----

    def test_set_retry_deadline_accepts_none(self) -> None:
        state = RebaseState(retry_deadline=5.0)
        assert state.set_retry_deadline(None).retry_deadline is None

    def test_set_retry_deadline_accepts_float(self) -> None:
        result = RebaseState().set_retry_deadline(1234.5)
        assert result.retry_deadline == 1234.5

    def test_set_retry_deadline_accepts_int_coerced_to_float(self) -> None:
        result = RebaseState().set_retry_deadline(42)
        assert result.retry_deadline == 42

    def test_set_retry_deadline_rejects_non_numeric(self) -> None:
        with pytest.raises(ValueError):
            RebaseState().set_retry_deadline("not a number")

    def test_set_retry_deadline_returns_new_instance(self) -> None:
        state = RebaseState()
        new = state.set_retry_deadline(1.0)
        assert new is not state
        assert state.retry_deadline is None  # original untouched

    # ----- record_consecutive_conflicts -----

    def test_record_consecutive_conflicts_zero_passes_through(self) -> None:
        assert RebaseState().record_consecutive_conflicts(0).consecutive_conflicts == 0

    def test_record_consecutive_conflicts_positive_passes_through(self) -> None:
        assert RebaseState().record_consecutive_conflicts(7).consecutive_conflicts == 7

    def test_record_consecutive_conflicts_negative_clamps_to_zero(self) -> None:
        assert RebaseState().record_consecutive_conflicts(-3).consecutive_conflicts == 0

    def test_record_consecutive_conflicts_oversized_clamps_to_cap(self) -> None:
        result = RebaseState().record_consecutive_conflicts(MAX_TRACKED_CONFLICT_ATTEMPTS + 100)
        assert result.consecutive_conflicts == MAX_TRACKED_CONFLICT_ATTEMPTS

    def test_record_consecutive_conflicts_exact_cap_is_allowed(self) -> None:
        result = RebaseState().record_consecutive_conflicts(MAX_TRACKED_CONFLICT_ATTEMPTS)
        assert result.consecutive_conflicts == MAX_TRACKED_CONFLICT_ATTEMPTS

    def test_record_consecutive_conflicts_none_treated_as_zero(self) -> None:
        result = RebaseState().record_consecutive_conflicts(None)
        assert result.consecutive_conflicts == 0

    def test_record_consecutive_conflicts_returns_new_instance(self) -> None:
        state = RebaseState(consecutive_conflicts=2)
        new = state.record_consecutive_conflicts(5)
        assert new is not state
        assert state.consecutive_conflicts == 2

    # ----- advance_conflict_strategy -----

    def test_advance_first_appends_strategy_and_increments_index(self) -> None:
        state = RebaseState(
            conflict_strategy_index=0,
            conflict_strategies_tried=(),
        )
        new = state.advance_conflict_strategy("step 1 reason")
        assert new.conflict_strategy_index == 1
        assert new.conflict_strategies_tried == ("rebase_resolver: step 1 reason",)
        assert new.resolution_exhausted is False
        assert new.resolution_exhaustion_reason is None

    def test_advance_subsequent_walks_ladder(self) -> None:
        state = RebaseState(
            conflict_strategy_index=0,
            conflict_strategies_tried=(),
        )
        for i, strategy in enumerate(CONFLICT_RESOLUTION_STRATEGIES):
            expected_count = i + 1
            state = state.advance_conflict_strategy(f"step-{i}")
            assert state.conflict_strategy_index == expected_count
            assert len(state.conflict_strategies_tried) == expected_count
            assert state.conflict_strategies_tried[-1] == f"{strategy}: step-{i}"
        assert state.resolution_exhausted is True
        assert state.resolution_exhaustion_reason is not None
        assert "; ".join(state.conflict_strategies_tried) == state.resolution_exhaustion_reason

    def test_advance_past_cap_keeps_index_at_cap(self) -> None:
        state = RebaseState(
            conflict_strategy_index=len(CONFLICT_RESOLUTION_STRATEGIES),
            conflict_strategies_tried=tuple(f"{s}: prior" for s in CONFLICT_RESOLUTION_STRATEGIES),
        )
        new = state.advance_conflict_strategy("after exhaustion")
        assert new.conflict_strategy_index == len(CONFLICT_RESOLUTION_STRATEGIES)
        assert len(new.conflict_strategies_tried) == len(CONFLICT_RESOLUTION_STRATEGIES)
        assert new.resolution_exhausted is True

    def test_advance_preserves_unrelated_fields(self) -> None:
        state = RebaseState(
            last_outcome=RebaseOutcomeKind.CONFLICT,
            last_target="main",
            consecutive_conflicts=2,
        )
        new = state.advance_conflict_strategy("step")
        assert new.last_outcome == RebaseOutcomeKind.CONFLICT
        assert new.last_target == "main"
        assert new.consecutive_conflicts == 2

    # ----- project_conflict_strategy_from -----

    def test_project_projects_all_four_fields(self) -> None:
        source = RebaseState(
            conflict_strategy_index=3,
            conflict_strategies_tried=("a: r1", "b: r2", "c: r3"),
            resolution_exhausted=True,
            resolution_exhaustion_reason="a: r1; b: r2; c: r3",
        )
        target = RebaseState(
            conflict_strategy_index=0,
            conflict_strategies_tried=(),
            resolution_exhausted=False,
            resolution_exhaustion_reason=None,
        )
        projected = target.project_conflict_strategy_from(source)
        assert projected.conflict_strategy_index == 3
        assert projected.conflict_strategies_tried == ("a: r1", "b: r2", "c: r3")
        assert projected.resolution_exhausted is True
        assert projected.resolution_exhaustion_reason == "a: r1; b: r2; c: r3"

    def test_project_does_not_mutate_source_or_target(self) -> None:
        source = RebaseState(
            conflict_strategy_index=2,
            conflict_strategies_tried=("s1: x", "s2: y"),
            resolution_exhausted=False,
        )
        target = RebaseState()
        _ = target.project_conflict_strategy_from(source)
        assert target.conflict_strategy_index == 0
        assert target.conflict_strategies_tried == ()
        assert source.conflict_strategy_index == 2

    def test_project_preserves_unrelated_fields_on_target(self) -> None:
        target = RebaseState(
            last_target="main",
            consecutive_conflicts=5,
        )
        source = RebaseState(
            conflict_strategy_index=2,
            conflict_strategies_tried=("s1", "s2"),
            resolution_exhausted=False,
        )
        projected = target.project_conflict_strategy_from(source)
        assert projected.last_target == "main"
        assert projected.consecutive_conflicts == 5

    # ----- set_intervention_required -----

    def test_set_intervention_required_default_records_intervention_required(self) -> None:
        new = RebaseState().set_intervention_required("operator must intervene")
        assert new.last_outcome == RebaseOutcomeKind.INTERVENTION_REQUIRED
        assert new.last_reason == "operator must intervene"

    def test_set_intervention_required_diagnostic_flag_records_variant(self) -> None:
        new = RebaseState().set_intervention_required("evidence diagnosis blocked", diagnostic=True)
        assert new.last_outcome == RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED
        assert new.last_reason == "evidence diagnosis blocked"

    def test_set_intervention_required_returns_new_instance(self) -> None:
        state = RebaseState(last_outcome=RebaseOutcomeKind.REBASED)
        new = state.set_intervention_required("stop the line")
        assert new is not state
        assert state.last_outcome == RebaseOutcomeKind.REBASED

    # ----- Cross-setter immutability -----

    def test_setter_chain_preserves_frozen_contract(self) -> None:
        state = RebaseState()
        chained = (
            state.set_retry_deadline(1.0)
            .record_consecutive_conflicts(2)
            .set_intervention_required("stop")
        )
        assert chained is not state
        assert chained.retry_deadline == 1.0
        assert chained.consecutive_conflicts == 2
        assert chained.last_outcome == RebaseOutcomeKind.INTERVENTION_REQUIRED
        # Original remains untouched.
        assert state.retry_deadline is None
        assert state.consecutive_conflicts == 0
        assert state.last_outcome is None
