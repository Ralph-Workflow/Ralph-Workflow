from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.auto_integrate import _configured_resolver_allowed
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_auto_integrate_regression_next_persisted_strategy_receives_budget": (
        "budget spend advances the strategy rung atomically; launch permission follows "
        "shared eligibility rather than resolution_exhausted"
    ),
}


def _available_ledger() -> AgentFailureLedger:
    return AgentFailureLedger(clock=FakeClock())


def test_suppressed_attempts_saturate_at_the_configured_strategy_budget() -> None:
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
    identity = ConflictIdentity(feature_sha="feature", target_sha="target")
    spent = apply_conflict_budget(
        RebaseState(last_action="conflict", last_target="main"),
        prior=RebaseState(),
        target="main",
        resolver_suppressed=False,
        identity=identity,
        attempts=1,
    )

    assert spent.conflict_strategy_index == 1
    assert (
        _configured_resolver_allowed(
            spent,
            "main",
            identity,
            ledger=_available_ledger(),
            candidates=("resolver",),
        )
        is True
    )

    restored = RebaseState.model_validate(spent.model_dump(mode="json"))

    assert restored.conflict_strategy_index == 1
    assert (
        _configured_resolver_allowed(
            restored,
            "main",
            identity,
            ledger=_available_ledger(),
            candidates=("resolver",),
        )
        is True
    )
