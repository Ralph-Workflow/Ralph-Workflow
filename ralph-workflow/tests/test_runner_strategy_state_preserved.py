"""Regression coverage for durable strategy state at runner integration seams."""

from __future__ import annotations

from ralph.pipeline import runner
from ralph.pipeline.rebase_state import RebaseState


def test_runner_keeps_reducer_strategy_progress_on_conflict_outcome() -> None:
    """A raw integration outcome cannot erase the reducer's next strategy rung."""
    reducer_rebase = RebaseState(
        conflict_strategy_index=1,
        conflict_strategies_tried=("rebase_resolver: unresolved shared.txt",),
    )
    outcome = RebaseState(last_action="conflict", last_reason="unresolved shared.txt")

    merged = runner._merge_strategy_onto_outcome(reducer_rebase, outcome)

    assert merged.last_action == "conflict"
    assert merged.conflict_strategy_index == 1
    assert merged.conflict_strategies_tried == reducer_rebase.conflict_strategies_tried
    assert merged.resolution_exhausted is False
