"""Regression coverage for clean-tree reconciliation during strategy escalation."""

from __future__ import annotations

from ralph.pipeline.auto_integrate_resolution_state import reconcile_stale_unresolved_state
from ralph.pipeline.rebase_state import RebaseState


def test_clean_reconciliation_keeps_in_progress_strategy_ladder() -> None:
    """A clean abort clears the latch without resetting the next strategy."""
    state = RebaseState(
        last_action="conflict",
        unresolved_integration_carried=True,
        conflict_strategy_index=1,
        conflict_strategies_tried=("rebase_resolver: unresolved shared.txt",),
    )

    reconciled = reconcile_stale_unresolved_state(state)

    assert reconciled.integration_unresolved is False
    assert reconciled.conflict_strategy_index == 1
    assert reconciled.conflict_strategies_tried == state.conflict_strategies_tried
