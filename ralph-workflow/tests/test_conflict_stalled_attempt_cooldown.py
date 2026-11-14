from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    CoolingDown,
    Eligible,
    Spent,
)

_PHASE = "rebase_conflict_resolution"


def _failed_attempt(agent: str) -> AttemptFact:
    return AttemptFact(phase=_PHASE, agent=agent, artifact_valid=False)


def test_stalled_attempts_enter_the_shared_cooldown_and_fallback_stays_eligible() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)

    ledger.record(_failed_attempt("primary"))

    primary = ledger.agent_eligibility(_PHASE, "primary")
    fallback = ledger.agent_eligibility(_PHASE, "fallback")
    assert isinstance(primary, CoolingDown)
    assert primary.until > 0
    assert isinstance(fallback, Eligible)


def test_cooldown_expires_without_private_state_mutation() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=1,
        base_cooldown_seconds=2.0,
    )
    ledger.record(_failed_attempt("primary"))

    assert isinstance(ledger.agent_eligibility(_PHASE, "primary"), CoolingDown)
    clock.advance(2.0)
    assert isinstance(ledger.agent_eligibility(_PHASE, "primary"), Eligible)


def test_total_limit_returns_the_canonical_spent_answer() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=2,
        total_limit=2,
    )

    ledger.record(_failed_attempt("primary"))
    outcome = ledger.record(_failed_attempt("primary"))

    assert isinstance(outcome.status, Spent)
    assert isinstance(ledger.agent_eligibility(_PHASE, "primary"), Spent)
