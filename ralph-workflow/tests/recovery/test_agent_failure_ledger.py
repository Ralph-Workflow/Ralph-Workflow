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
    assert outcome.status.until_ms == 70000
    assert outcome.status.reason == "consecutive_unproductive_limit_reached"

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

    clock.advance(59.999)
    assert isinstance(ledger.status("development", "agent-a"), Cooldown)

    clock.advance(0.001)
    status = ledger.status("development", "agent-a")
    assert isinstance(status, Eligible)
    assert ledger.is_available("development", "agent-a")

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

    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    outcome1 = ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    assert isinstance(outcome1.status, Cooldown)
    assert outcome1.status.until_ms == 30000

    clock.advance(30.0)
    assert isinstance(ledger.status("dev", "a"), Eligible)

    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    outcome2 = ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    assert isinstance(outcome2.status, Cooldown)
    assert outcome2.status.until_ms == 30000 + 60000

    clock.advance(60.0)
    assert isinstance(ledger.status("dev", "a"), Eligible)

    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    outcome3 = ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.status.until_ms == 90000 + 100000


def test_workspace_changed_resets_consecutive_count() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

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
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(outcome3.status, Eligible)
    assert not outcome3.demoted
    assert ledger.is_available("dev", "a")

    outcome4 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome4.status, Eligible)
    outcome5 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome5.status, Eligible)

    outcome6 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome6.status, Cooldown)
    assert outcome6.demoted


def test_total_limit_demotes_agent_to_spent() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    for _ in range(8):
        outcome = ledger.record(
            AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
        )
        assert isinstance(outcome.status, Eligible)
        assert not outcome.demoted
        assert ledger.is_available("dev", "a")

    outcome9 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(outcome9.status, Spent)
    assert outcome9.demoted
    assert outcome9.reason == "total_unproductive_limit_reached"
    assert outcome9.status.reason == "total_unproductive_limit_reached"

    status = ledger.status("dev", "a")
    assert isinstance(status, Spent)
    assert status.is_spent
    assert not status.is_eligible
    assert not status.is_cooldown
    assert not ledger.is_available("dev", "a")

    clock.advance(100_000.0)
    assert isinstance(ledger.status("dev", "a"), Spent)


def test_total_limit_reached_on_non_workspace_change() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock, consecutive_limit=3, total_limit=9, base_cooldown_seconds=10.0
    )

    for _ in range(2):
        for _ in range(3):
            ledger.record(
                AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
            )
        clock.advance(100.0)

    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )

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

    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )

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

    for _ in range(3):
        ledger.record(AttemptFact(phase="p1", agent="a", artifact_valid=False))

    assert isinstance(ledger.status("p1", "a"), Cooldown)
    assert isinstance(ledger.status("p1", "b"), Eligible)
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

    for _ in range(3):
        ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))

    for _ in range(9):
        ledger.record(
            AttemptFact(phase="dev", agent="b", artifact_valid=False, workspace_changed=True)
        )

    for _ in range(2):
        ledger.record(AttemptFact(phase="dev", agent="c", artifact_valid=False))

    snap = ledger.snapshot()
    assert snap["consecutive_limit"] == 3
    assert snap["total_limit"] == 9
    assert snap["base_cooldown_seconds"] == 50.0
    assert snap["max_cooldown_seconds"] == 500.0
    assert snap["backoff_factor"] == 3.0

    restored = AgentFailureLedger.from_snapshot(snap, clock=clock)

    assert restored.consecutive_limit == 3
    assert restored.total_limit == 9
    assert restored.base_cooldown_seconds == 50.0
    assert restored.max_cooldown_seconds == 500.0
    assert restored.backoff_factor == 3.0

    status_a = restored.status("dev", "a")
    assert isinstance(status_a, Cooldown)
    assert status_a.until_ms == 150000

    status_b = restored.status("dev", "b")
    assert isinstance(status_b, Spent)
    assert status_b.reason == "total_unproductive_limit_reached"

    status_c = restored.status("dev", "c")
    assert isinstance(status_c, Eligible)

    outcome_c = restored.record(AttemptFact(phase="dev", agent="c", artifact_valid=False))
    assert outcome_c.demoted is True
    assert isinstance(outcome_c.status, Cooldown)

    clock.advance(50.0)
    assert isinstance(restored.status("dev", "a"), Eligible)

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

    assert ledger.earliest_wait_ms("dev", ["a", "b"]) == 0

    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))
    ledger.record(AttemptFact(phase="dev", agent="a", artifact_valid=False))

    assert ledger.earliest_wait_ms("dev", ["a", "b"]) == 0

    clock.advance(10.0)
    ledger.record(AttemptFact(phase="dev", agent="b", artifact_valid=False))
    ledger.record(AttemptFact(phase="dev", agent="b", artifact_valid=False))

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


_FIVE_DAYS_S = 5 * 86400.0
_WALL_START_S = 1_800_000_000.0


def _cooled_down_snapshot(clock: FakeClock) -> dict[str, object]:
    ledger = AgentFailureLedger(clock=clock, base_cooldown_seconds=60.0)
    for _ in range(3):
        ledger.record(AttemptFact(phase="development", agent="claude", artifact_valid=False))
    assert ledger.earliest_wait_ms("development", ["claude"]) == 60_000
    return ledger.snapshot()


def test_cooldown_restored_after_reboot_keeps_its_remaining_duration() -> None:
    snap = _cooled_down_snapshot(FakeClock(start=_FIVE_DAYS_S, epoch_start=_WALL_START_S))
    after_reboot = FakeClock(start=120.0, epoch_start=_WALL_START_S + 20.0)

    restored = AgentFailureLedger.from_snapshot(snap, clock=after_reboot)

    assert restored.earliest_wait_ms("development", ["claude"]) == 40_000
    after_reboot.advance(40.0)
    assert isinstance(restored.status("development", "claude"), Eligible)


def test_cooldown_that_elapsed_across_reboot_restores_eligible() -> None:
    snap = _cooled_down_snapshot(FakeClock(start=_FIVE_DAYS_S, epoch_start=_WALL_START_S))
    after_reboot = FakeClock(start=120.0, epoch_start=_WALL_START_S + 130.0)

    restored = AgentFailureLedger.from_snapshot(snap, clock=after_reboot)

    assert isinstance(restored.status("development", "claude"), Eligible)
    assert restored.earliest_wait_ms("development", ["claude"]) == 0


def test_legacy_monotonic_only_cooldown_is_clamped_to_max_cooldown() -> None:
    snap = _cooled_down_snapshot(FakeClock(start=_FIVE_DAYS_S, epoch_start=_WALL_START_S))
    raw_entries = snap["entries"]
    assert isinstance(raw_entries, dict)
    legacy_entries: dict[str, object] = {}
    for key, item in raw_entries.items():
        assert isinstance(item, dict)
        legacy_entries[key] = {
            field: value
            for field, value in item.items()
            if field in {"phase", "agent", "consecutive_unproductive", "total_unproductive"}
            or field in {"cooldown_until_ms", "cooldown_reason", "cooldown_count", "is_spent"}
        }
    legacy = {**snap, "entries": legacy_entries}
    after_reboot = FakeClock(start=120.0, epoch_start=_WALL_START_S + 20.0)

    restored = AgentFailureLedger.from_snapshot(legacy, clock=after_reboot)

    wait_ms = restored.earliest_wait_ms("development", ["claude"])
    assert 0 < wait_ms <= int(restored.max_cooldown_seconds * 1000)
