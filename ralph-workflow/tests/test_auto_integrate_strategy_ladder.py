"""Regression coverage for the durable per-strategy conflict budget."""

from __future__ import annotations

from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.rebase_state import RebaseState


def test_auto_integrate_regression_next_persisted_strategy_receives_budget() -> None:
    """DA-002: one exhausted rung cannot suppress a distinct strategy after restart."""
    identity = ConflictIdentity(feature_sha="feature", target_sha="target")
    spent = apply_conflict_budget(
        RebaseState(last_action="conflict", last_target="main"),
        prior=RebaseState(),
        target="main",
        resolver_suppressed=False,
        identity=identity,
        attempts=1,
    )

    assert resolver_allowed(spent, "main", identity, attempts=1) is False

    restored = RebaseState.model_validate(
        spent.model_copy(update={"conflict_strategy_index": 1}).model_dump(mode="json")
    )

    assert resolver_allowed(restored, "main", identity, attempts=1) is True
