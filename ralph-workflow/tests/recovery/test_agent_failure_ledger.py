from __future__ import annotations

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    AttemptOutcome,
    Cooldown,
    Eligible,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

UNPRODUCTIVE_LIMIT = str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_initial_status_is_eligible": "operator rule: an agent is either on cooldown or not; AgentStatus.is_spent no longer exists, so the initial status check drops the spent assertion",
    "test_consecutive_limit_triggers_cooldown": "operator rule: an agent is either on cooldown or not; the consecutive limit now starts a cooldown with the single unproductive_limit reason instead of consecutive_unproductive_limit_reached",
    "test_u2_regression_ledger_cooldown_logs_one_complete_owner_decision": "operator rule: an agent is either on cooldown or not; the ledger-owned limit cooldown is now logged with reason unproductive_limit and its unproductive attempt counts",
    "test_total_limit_starts_an_expiring_cooldown": "operator rule: an agent is either on cooldown or not; the total unproductive limit now starts an expiring unproductive_limit cooldown instead of a permanent Spent state, so the test asserts the cooldown and its expiry",
    "test_total_limit_reached_on_non_workspace_change": "operator rule: an agent is either on cooldown or not; reaching a limit resets both counters, so the total limit is reached through workspace-changing attempts that keep the consecutive count below its limit and yields an unproductive_limit cooldown instead of Spent",
    "test_free_retry_category_treated_identically": "operator rule: an agent is either on cooldown or not; the limit cooldown reason is now unproductive_limit",
    "test_independent_tracking_per_phase_and_agent": "operator rule: an agent is either on cooldown or not; the cooldown tracker is keyed by agent only, so a cooldown recorded in one phase applies to that agent in every phase while other agents stay eligible",
    "test_snapshot_round_trip": "operator rule: an agent is either on cooldown or not; ledger snapshots now carry only counters and cooldown_count while cooldown windows live in the shared tracker, so the round trip asserts restored counters and backoff instead of restored Cooldown and Spent statuses",
    "test_attempt_outcome_properties": "operator rule: an agent is either on cooldown or not; Spent and AttemptOutcome.demoted no longer exist, so only the eligible and cooldown outcomes are asserted via cooldown_started",
    "test_cooldown_restored_after_reboot_keeps_its_remaining_duration": "operator rule: an agent is either on cooldown or not; the unproductive_limit cooldown now lives in the controller tracker, so the reboot contract is asserted through the controller snapshot",
    "test_cooldown_that_elapsed_across_reboot_restores_eligible": "operator rule: an agent is either on cooldown or not; the unproductive_limit cooldown now lives in the controller tracker, so the reboot contract is asserted through the controller snapshot",
    "test_restored_cooldown_is_clamped_to_its_reason_cap": "operator rule: an agent is either on cooldown or not; the ledger snapshot no longer has cooldown fields, so the legacy clamp test asserts a restored controller cooldown never exceeds the one hour unproductive_limit cap",
}


def test_initial_status_is_eligible() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock)
    status = ledger.status("development", "agent-a")
    assert isinstance(status, Eligible)
    assert status.is_eligible
    assert not status.is_cooldown
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
    assert not outcome1.cooldown_started
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
    assert not outcome2.cooldown_started
    assert outcome2.reason is None
    assert ledger.is_available("development", "agent-a")

    outcome3 = ledger.record(fact2)
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.cooldown_started
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
    assert outcome.cooldown_started
    assert outcome.reason == UNPRODUCTIVE_LIMIT
    assert outcome.status.until_ms == 70000
    assert outcome.status.reason == UNPRODUCTIVE_LIMIT

    status = ledger.status("development", "agent-a")
    assert isinstance(status, Cooldown)
    assert status.is_cooldown
    assert not ledger.is_available("development", "agent-a")


def test_u2_regression_ledger_cooldown_logs_one_complete_owner_decision() -> None:
    clock = FakeClock(start=10.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            agent_max_consecutive_unproductive=3,
            agent_max_total_unproductive=9,
        )
    )
    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")
    try:
        for _ in range(3):
            controller.record_attempt_fact(
                "development",
                "agent-a",
                artifact_valid=False,
                workspace_changed=False,
            )
    finally:
        logger.remove(sink_id)

    cooldown_lines = [line for line in logs if "cooldown_decision" in line]
    assert len(cooldown_lines) == 1
    assert "phase=development" in cooldown_lines[0]
    assert "agent=agent-a" in cooldown_lines[0]
    assert "reason=unproductive_limit" in cooldown_lines[0]
    assert "duration_ms=60000" in cooldown_lines[0]
    assert "expiry_ms=70000" in cooldown_lines[0]
    assert "unproductive_attempts consecutive=3 total=3" in cooldown_lines[0]


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
    assert not outcome1.cooldown_started
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
    assert not outcome2.cooldown_started

    outcome3 = ledger.record(
        AttemptFact(
            phase="development",
            agent="agent-a",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.cooldown_started


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
    assert not outcome1.cooldown_started
    outcome2 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome2.status, Eligible)
    assert not outcome2.cooldown_started

    outcome3 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(outcome3.status, Eligible)
    assert not outcome3.cooldown_started
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
    assert outcome6.cooldown_started


def test_total_limit_starts_an_expiring_cooldown() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock, consecutive_limit=3, total_limit=9, base_cooldown_seconds=60.0
    )

    for _ in range(8):
        outcome = ledger.record(
            AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
        )
        assert isinstance(outcome.status, Eligible)
        assert not outcome.cooldown_started
        assert ledger.is_available("dev", "a")

    outcome9 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(outcome9.status, Cooldown)
    assert outcome9.cooldown_started
    assert outcome9.reason == UNPRODUCTIVE_LIMIT
    assert outcome9.status.until_ms == 60_000

    status = ledger.status("dev", "a")
    assert isinstance(status, Cooldown)
    assert status.is_cooldown
    assert not status.is_eligible
    assert not ledger.is_available("dev", "a")

    clock.advance(60.0)
    assert isinstance(ledger.status("dev", "a"), Eligible)
    after_expiry = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=True)
    )
    assert isinstance(after_expiry.status, Eligible)


def test_total_limit_reached_on_non_workspace_change() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(
        clock=clock, consecutive_limit=3, total_limit=9, base_cooldown_seconds=10.0
    )

    outcome = None
    for _ in range(3):
        for workspace_changed in (True, False, False):
            outcome = ledger.record(
                AttemptFact(
                    phase="dev",
                    agent="a",
                    artifact_valid=False,
                    workspace_changed=workspace_changed,
                )
            )

    assert outcome is not None
    assert isinstance(outcome.status, Cooldown)
    assert outcome.cooldown_started
    assert outcome.reason == UNPRODUCTIVE_LIMIT
    assert outcome.status.until_ms == 10_000
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
    assert not outcome.cooldown_started

    outcome1 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome1.status, Eligible)
    assert not outcome1.cooldown_started
    outcome2 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome2.status, Eligible)
    assert not outcome2.cooldown_started
    outcome3 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, workspace_changed=False)
    )
    assert isinstance(outcome3.status, Cooldown)
    assert outcome3.cooldown_started


def test_free_retry_category_treated_identically() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    o1 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, free_retry_category=True)
    )
    assert isinstance(o1.status, Eligible)
    assert not o1.cooldown_started

    o2 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, free_retry_category=False)
    )
    assert isinstance(o2.status, Eligible)
    assert not o2.cooldown_started

    o3 = ledger.record(
        AttemptFact(phase="dev", agent="a", artifact_valid=False, free_retry_category=True)
    )
    assert isinstance(o3.status, Cooldown)
    assert o3.cooldown_started
    assert o3.reason == UNPRODUCTIVE_LIMIT


def test_independent_tracking_per_phase_and_agent() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3, total_limit=9)

    for _ in range(3):
        ledger.record(AttemptFact(phase="p1", agent="a", artifact_valid=False))

    assert isinstance(ledger.status("p1", "a"), Cooldown)
    assert isinstance(ledger.status("p1", "b"), Eligible)
    assert isinstance(ledger.status("p2", "a"), Cooldown)
    assert isinstance(ledger.status("p2", "b"), Eligible)
    second_phase_attempt = ledger.record(AttemptFact(phase="p2", agent="a", artifact_valid=False))
    assert not second_phase_attempt.cooldown_started


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

    original_a = ledger.status("dev", "a")
    assert isinstance(original_a, Cooldown)
    assert original_a.until_ms == 150000
    original_b = ledger.status("dev", "b")
    assert isinstance(original_b, Cooldown)
    assert original_b.reason == UNPRODUCTIVE_LIMIT

    entries = snap["entries"]
    assert isinstance(entries, dict)
    assert entries["dev:a"] == {
        "phase": "dev",
        "agent": "a",
        "consecutive_unproductive": 0,
        "total_unproductive": 0,
        "cooldown_count": 1,
    }
    assert entries["dev:c"] == {
        "phase": "dev",
        "agent": "c",
        "consecutive_unproductive": 2,
        "total_unproductive": 2,
        "cooldown_count": 0,
    }

    status_c = restored.status("dev", "c")
    assert isinstance(status_c, Eligible)

    outcome_c = restored.record(AttemptFact(phase="dev", agent="c", artifact_valid=False))
    assert outcome_c.cooldown_started is True
    assert isinstance(outcome_c.status, Cooldown)
    assert outcome_c.status.until_ms == 150000

    outcomes_a = [
        restored.record(AttemptFact(phase="dev", agent="a", artifact_valid=False)) for _ in range(3)
    ]
    outcome_a = outcomes_a[-1]
    assert isinstance(outcome_a.status, Cooldown)
    assert outcome_a.status.until_ms == 100000 + 150000

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
    assert not eligible_outcome.cooldown_started
    assert eligible_outcome.reason is None

    cooldown_outcome = AttemptOutcome(
        Cooldown(until_ms=1000, reason="test_reason"),
        3,
        3,
    )
    assert cooldown_outcome.cooldown_started
    assert cooldown_outcome.reason == "test_reason"


_FIVE_DAYS_S = 5 * 86400.0
_WALL_START_S = 1_800_000_000.0


def _cooled_down_snapshot(clock: FakeClock) -> dict[str, object]:
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))
    for _ in range(3):
        controller.record_attempt_fact(
            "development", "claude", artifact_valid=False, workspace_changed=False
        )
    assert controller.earliest_eligible_wait_ms("development", ["claude"]) == 60_000
    return controller.snapshot()


def test_cooldown_restored_after_reboot_keeps_its_remaining_duration() -> None:
    snap = _cooled_down_snapshot(FakeClock(start=_FIVE_DAYS_S, epoch_start=_WALL_START_S))
    after_reboot = FakeClock(start=120.0, epoch_start=_WALL_START_S + 20.0)

    restored = RecoveryController.from_snapshot(snap, clock=after_reboot)

    assert restored.earliest_eligible_wait_ms("development", ["claude"]) == 40_000
    after_reboot.advance(40.0)
    assert isinstance(restored.agent_eligibility("development", "claude"), Eligible)


def test_cooldown_that_elapsed_across_reboot_restores_eligible() -> None:
    snap = _cooled_down_snapshot(FakeClock(start=_FIVE_DAYS_S, epoch_start=_WALL_START_S))
    after_reboot = FakeClock(start=120.0, epoch_start=_WALL_START_S + 130.0)

    restored = RecoveryController.from_snapshot(snap, clock=after_reboot)

    assert isinstance(restored.agent_eligibility("development", "claude"), Eligible)
    assert restored.earliest_eligible_wait_ms("development", ["claude"]) == 0


def test_restored_cooldown_is_clamped_to_its_reason_cap() -> None:
    snap = _cooled_down_snapshot(FakeClock(start=_FIVE_DAYS_S, epoch_start=_WALL_START_S))
    after_reboot = FakeClock(start=120.0, epoch_start=_WALL_START_S + 20.0)

    restored = RecoveryController.from_snapshot(snap, clock=after_reboot)

    wait_ms = restored.earliest_eligible_wait_ms("development", ["claude"])
    assert wait_ms is not None
    assert 0 < wait_ms <= 3_600_000
