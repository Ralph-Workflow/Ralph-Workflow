"""Unit tests for AgentFailureLedger."""

from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    AttemptOutcome,
    Cooldown,
    Eligible,
    Spent,
)


def test_initial_status_is_eligible() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock)
    status = ledger.status("development", "agent-a")
    assert isinstance(status, Eligible)
    assert status.is_eligible
    assert not status.is_cooldown
    assert not status.is_spent
    assert ledger.is_available("development", "agent-a")


def test_unproductive_attempts_remain_eligible_until_consecutive_limit() -> None:
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    fact1 = AttemptFact(
        phase="development",
        agent="agent-a",
        artifact_valid=False,
        workspace_changed=False,
    )
    outcome1 = ledger.record(fact1)
    assert isinstance(outcome1.status, Eligible)
    assert not outcome1.demoted
    assert outcome1.reason is None
    assert ledger.is_available("development", "agent-a")

    fact2 = AttemptFact(
        phase="development",
        agent="agent-a",
        artifact_valid=False,
        workspace_changed=False,
    )
    outcome2 = ledger.record(fact2)
    assert isinstance(outcome2.status, Eligible)
    assert not outcome2.demoted
    assert outcome2.reason is None
    assert ledger.is_available("development", "agent-a")

    # 3rd failure reaches consecutive limit -> demoted to Cooldown
    outcome3 = ledger.record(fact2)
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.demoted
    assert not ledger.is_available("development", "agent-a")


def test_consecutive_limit_triggers_cooldown() -> None:
    clock = FakeClock(start=10.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=3,
        total_limit=9,
        base_cooldown_seconds=60.0,
    )

    for _ in range(2):
        ledger.record(
            AttemptFact(
                phase="development",
                agent="agent-a",
                artifact_valid=False,
                workspace_changed=False,
            )
        )

    # 3rd consecutive unproductive attempt
    outcome = ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome.status, Cooldown)
    assert outcome.demoted
    assert outcome.reason == "consecutive_unproductive_limit_reached"
    # 10.0 s + 60.0 s = 70.0 s -> 70000 ms
    assert outcome.status.until_ms == 70000
    assert outcome.status.reason == "consecutive_unproductive_limit_reached"

    # Status query returns Cooldown
    status = ledger.status("development", "agent-a")
    assert isinstance(status, Cooldown)
    assert status.is_cooldown
    assert not ledger.is_available("development", "agent-a")


def test_cooldown_expiry_resets_consecutive_and_returns_eligible() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=3,
        total_limit=9,
        base_cooldown_seconds=60.0,
    )

    for _ in range(3):
        ledger.record(
            AttemptFact(
                phase="development",
                agent="agent-a",
                artifact_valid=False,
                workspace_changed=False,
            )
        )

    assert isinstance(ledger.status("development", "agent-a"), Cooldown)

    # Advance clock to just before expiration
    clock.advance(59.999)
    assert isinstance(ledger.status("development", "agent-a"), Cooldown)

    # Advance clock to expiration
    clock.advance(0.001)
    status = ledger.status("development", "agent-a")
    assert isinstance(status, Eligible)
    assert ledger.is_available("development", "agent-a")

    # Next unproductive attempt after expiry: agent starts a fresh consecutive streak
    outcome1 = ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome1.status, Eligible)
    assert not outcome1.demoted
    assert ledger.is_available("development", "agent-a")

    outcome2 = ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome2.status, Eligible)
    assert not outcome2.demoted

    # 3rd consecutive attempt triggers cooldown again, proving streak started fresh at 1
    outcome3 = ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.demoted


def test_growing_backoff_on_subsequent_cooldowns() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=2,
        total_limit=10,
        base_cooldown_seconds=30.0,
        max_cooldown_seconds=100.0,
        backoff_factor=2.0,
    )

    # Streak 1: 2 failures -> Cooldown 1 (30s)
    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    outcome1 = ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    assert isinstance(outcome1.status, Cooldown)
    assert outcome1.status.until_ms == 30000  # 0 + 30s

    # Expire streak 1
    clock.advance(30.0)
    assert isinstance(ledger.status("dev", "a"), Eligible)

    # Streak 2: 2 failures -> Cooldown 2 (30 * 2 = 60s)
    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    outcome2 = ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    assert isinstance(outcome2.status, Cooldown)
    assert outcome2.status.until_ms == 30000 + 60000  # 30s + 60s = 90000ms

    # Expire streak 2
    clock.advance(60.0)
    assert isinstance(ledger.status("dev", "a"), Eligible)

    # Streak 3: 2 failures -> Cooldown 3 (30 * 4 = 120s capped at max 100s)
    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    outcome3 = ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.status.until_ms == 90000 + 100000  # 90s + 100s = 190000ms


def test_workspace_changed_resets_consecutive_count() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    # 2 failures without workspace change: remains eligible
    outcome1 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome1.status, Eligible)
    assert not outcome1.demoted
    outcome2 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome2.status, Eligible)
    assert not outcome2.demoted

    # 3rd failure with workspace change -> resets consecutive count, agent remains eligible
    # (if consecutive had not reset, attempt 3 would have triggered cooldown)
    outcome3 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(outcome3.status, Eligible)
    assert not outcome3.demoted
    assert ledger.is_available("dev", "a")

    # 2 more failures without workspace change: remains eligible
    outcome4 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome4.status, Eligible)
    outcome5 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome5.status, Eligible)

    # 3rd failure without workspace change (attempt 6 overall) triggers cooldown
    outcome6 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome6.status, Cooldown)
    assert outcome6.demoted


def test_total_limit_demotes_agent_to_spent() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    # 8 failures with workspace changed: not demoted after 3, remains eligible
    for _ in range(8):
        outcome = ledger.record(
            AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
        )
        assert isinstance(outcome.status, Eligible)
        assert not outcome.demoted
        assert ledger.is_available("dev", "a")

    # 9th failure reaches total_limit: demoted to Spent
    outcome9 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(outcome9.status, Spent)
    assert outcome9.demoted
    assert outcome9.reason == "total_unproductive_limit_reached"
    assert outcome9.status.reason == "total_unproductive_limit_reached"

    # Status query returns Spent
    status = ledger.status("dev", "a")
    assert isinstance(status, Spent)
    assert status.is_spent
    assert not status.is_eligible
    assert not status.is_cooldown
    assert not ledger.is_available("dev", "a")

    # Advancing clock does NOT expire Spent status
    clock.advance(100_000.0)
    assert isinstance(ledger.status("dev", "a"), Spent)


def test_total_limit_reached_on_non_workspace_change() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock, consecutive_limit=3, total_limit=9, base_cooldown_seconds=10.0
    )

    # 2 cycles of 3 failures (total = 6)
    for _ in range(2):
        for _ in range(3):
            ledger.record(
                AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
            )
        clock.advance(100.0)

    # 7th and 8th attempts
    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )

    # 9th attempt: total hits 9 -> Spent takes precedence over Cooldown
    outcome = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome.status, Spent)
    assert outcome.demoted
    assert outcome.reason == "total_unproductive_limit_reached"
    assert not ledger.is_available("dev", "a")


def test_artifact_valid_clears_all_history() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=3,
        total_limit=9,
        base_cooldown_seconds=60.0,
    )

    # 2 unproductive attempts
    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )

    # Successful attempt with valid artifact
    outcome = ledger.record(
        AttemptFact(
            phase="dev",
            agent="a",
            artifact_valid=True,
            workspace_changed=True,
        )
    )
    assert isinstance(outcome.status, Eligible)
    assert not outcome.demoted

    # Verify history is completely fresh: next 2 failures leave agent eligible, 3rd demotes to Cooldown
    outcome1 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome1.status, Eligible)
    assert not outcome1.demoted
    outcome2 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome2.status, Eligible)
    assert not outcome2.demoted
    outcome3 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.demoted


def test_free_retry_category_treated_identically() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    # Free retry category attempts still count as unproductive and demote after consecutive_limit
    o1 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, free_retry_category=True)
    )
    assert isinstance(o1.status, Eligible)
    assert not o1.demoted

    o2 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, free_retry_category=False)
    )
    assert isinstance(o2.status, Eligible)
    assert not o2.demoted

    o3 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, free_retry_category=True)
    )
    assert isinstance(o3.status, Cooldown)
    assert o3.demoted
    assert o3.reason == "consecutive_unproductive_limit_reached"


def test_independent_tracking_per_phase_and_agent() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    # Agent A in phase 1 fails 3 times -> Cooldown
    for _ in range(3):
        ledger.record(AttemptFact(phase="p1", agent="a", artifact_valid=False))

    assert isinstance(ledger.status("p1", "a"), Cooldown)
    # Agent B in phase 1 remains Eligible
    assert isinstance(ledger.status("p1", "b"), Eligible)
    # Agent A in phase 2 remains Eligible
    assert isinstance(ledger.status("p2", "a"), Eligible)


def test_snapshot_round_trip() -> None:
    clock = FakeClock(start=100.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=3,
        total_limit=9,
        base_cooldown_seconds=50.0,
        max_cooldown_seconds=500.0,
        backoff_factor=3.0,
    )

    # Agent A: 3 failures -> Cooldown until 100s + 50s = 150000ms
    for _ in range(3):
        ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))

    # Agent B: 9 failures with workspace change -> Spent
    for _ in range(9):
        ledger.record(
            AttemptFact(phase="dev", agent="b", artifact_valid=False, workspace_changed=True)
        )

    # Agent C: 2 failures -> Eligible (consecutive=2, total=2)
    for _ in range(2):
        ledger.record(AttemptFact(phase="dev", agent="c", artifact_valid=False))

    snap = ledger.snapshot()
    assert snap["consecutive_limit"] == 3
    assert snap["total_limit"] == 9
    assert snap["base_cooldown_seconds"] == 50.0
    assert snap["max_cooldown_seconds"] == 500.0
    assert snap["backoff_factor"] == 3.0

    # Restore in a new ledger with the same clock state
    restored = AgentFailureLedger.from_snapshot(snap, clock=clock)

    assert restored.consecutive_limit == 3
    assert restored.total_limit == 9
    assert restored.base_cooldown_seconds == 50.0
    assert restored.max_cooldown_seconds == 500.0
    assert restored.backoff_factor == 3.0

    # Status matches exactly
    status_a = restored.status("dev", "a")
    assert isinstance(status_a, Cooldown)
    assert status_a.until_ms == 150000

    status_b = restored.status("dev", "b")
    assert isinstance(status_b, Spent)
    assert status_b.reason == "total_unproductive_limit_reached"

    status_c = restored.status("dev", "c")
    assert isinstance(status_c, Eligible)

    # Agent C had 2 failures prior to snapshot. Recording 1 more unproductive
    # attempt on restored ledger reaches the consecutive limit of 3 and demotes
    # Agent C to Cooldown, behaviorally proving streak state was preserved.
    outcome_c = restored.record(AttemptFact(phase="dev", agent="c", artifact_valid=False))
    assert outcome_c.demoted is True
    assert isinstance(outcome_c.status, Cooldown)

    # Advance clock past cooldown: restored ledger expires cooldown accurately
    clock.advance(50.0)
    assert isinstance(restored.status("dev", "a"), Eligible)

    # Snapshots match
    snap2 = restored.snapshot()
    assert snap2["consecutive_limit"] == snap["consecutive_limit"]
    assert snap2["total_limit"] == snap["total_limit"]


def test_earliest_wait_ms() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock,
        consecutive_limit=2,
        base_cooldown_seconds=30.0,
    )

    # If any agent is eligible, earliest wait is 0
    assert ledger.earliest_wait_ms("dev", ["a", "b"]) == 0

    # Agent A goes on cooldown for 30s (until 30,000ms)
    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))

    # Agent B is still eligible
    assert ledger.earliest_wait_ms("dev", ["a", "b"]) == 0

    # Agent B goes on cooldown for 30s at time 10s (until 40,000ms)
    clock.advance(10.0)
    ledger.record(AttemptFact(phase="dev", agent="b", artifact_valid=False))
    ledger.record(AttemptFact(phase="dev", agent="b", artifact_valid=False))

    # At time 10s: A expires in 20s (20,000ms), B expires in 30s (30,000ms) -> min is 20,000ms
    assert ledger.earliest_wait_ms("dev", ["a", "b"]) == 20000


def test_invalid_init_arguments_raise_value_error() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError, match="consecutive_limit must be positive"):
        AgentFailureLedger(clock=clock, consecutive_limit=0)

    with pytest.raises(ValueError, match="total_limit must be positive"):
        AgentFailureLedger(clock=clock, total_limit=0)

    with pytest.raises(ValueError, match="base_cooldown_seconds must be positive"):
        AgentFailureLedger(clock=clock, base_cooldown_seconds=0.0)

    with pytest.raises(ValueError, match="max_cooldown_seconds .* must be greater"):
        AgentFailureLedger(clock=clock, base_cooldown_seconds=60.0, max_cooldown_seconds=30.0)

    with pytest.raises(ValueError, match="backoff_factor must be >= 1.0"):
        AgentFailureLedger(clock=clock, backoff_factor=0.5)


def test_attempt_outcome_properties() -> None:
    eligible_outcome = AttemptOutcome(Eligible(), 0, 0)
    assert not eligible_outcome.demoted
    assert eligible_outcome.reason is None

    cooldown_outcome = AttemptOutcome(
        Cooldown(until_ms=1000, reason="test_reason"),
        3,
        3,
    )
    assert cooldown_outcome.demoted
    assert cooldown_outcome.reason == "test_reason"

    spent_outcome = AttemptOutcome(
        Spent(reason="spent_reason"),
        0,
        9,
    )
    assert spent_outcome.demoted
    assert spent_outcome.reason == "spent_reason"
