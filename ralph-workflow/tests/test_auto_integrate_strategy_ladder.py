"""Regression coverage for the durable per-strategy conflict bookkeeping."""

from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger


def _available_ledger() -> AgentFailureLedger:
    """A ledger with no recorded agent failure: every candidate is eligible."""
    return AgentFailureLedger(clock=FakeClock())


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
    """DA-002: one exhausted rung cannot suppress a distinct strategy after restart.

    The recorded rung is repository state: it refuses the resolver while
    the SAME rung is still the conflict on disk. The next rung is a
    different rung, so the recorded exhaustion does not carry over and
    the resolver is reachable again. Agent health is a separate
    question, and the failure ledger answers it.
    """
    identity = ConflictIdentity(feature_sha="feature", target_sha="target")
    spent = apply_conflict_budget(
        RebaseState(last_action="conflict", last_target="main"),
        prior=RebaseState(),
        target="main",
        resolver_suppressed=False,
        identity=identity,
        attempts=1,
    )

    exhausted = spent.model_copy(update={"resolution_exhausted": True})
    assert resolver_allowed(exhausted, "main", identity, ledger=_available_ledger()) is False

    restored = RebaseState.model_validate(
        spent.model_copy(
            update={"conflict_strategy_index": 1, "resolution_exhausted": True}
        ).model_dump(mode="json")
    )

    assert resolver_allowed(restored, "main", identity, ledger=_available_ledger()) is True
