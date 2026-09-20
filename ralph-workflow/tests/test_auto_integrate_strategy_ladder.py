"""Regression coverage for the durable per-strategy conflict budget."""

from __future__ import annotations

from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.rebase_state import RebaseState


def test_suppressed_attempts_saturate_at_the_configured_strategy_budget() -> None:
    """Repeated suppressed seams cannot churn an ever-growing attempt count."""
    identity = ConflictIdentity(feature_sha="feature", target_sha="target")
    state = RebaseState(
        last_action="conflict",
        last_target="main",
        consecutive_conflicts=2,
        last_conflict_feature_sha="feature",
        last_conflict_target_sha="target",
    )

    for _ in range(3):
        state = apply_conflict_budget(
            RebaseState(last_action="conflict", last_target="main"),
            prior=state,
            target="main",
            resolver_suppressed=True,
            identity=identity,
            attempts=2,
        )

    assert state.consecutive_conflicts == 2
    assert state.last_reason == (
        "conflict resolution budget exhausted for 'main' after 2 unresolved attempts; "
        "escalating to the next resolution strategy"
    )


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
