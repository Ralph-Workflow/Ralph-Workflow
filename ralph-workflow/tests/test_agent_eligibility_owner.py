from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    AttemptFact,
    Cooldown,
    Eligible,
    WaitKind,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_all_limit_reached_agents_cool_down_with_a_finite_wait_across_projections": "operator rule: an agent is either on cooldown or not; agents past the total unproductive limit are on an expiring unproductive_limit cooldown instead of a permanent Spent state, so every projection reports a finite wait and selection resumes after expiry",
    "test_cooling_down_has_positive_wait_and_is_not_selected": "operator rule: an agent is either on cooldown or not; the ledger snapshot no longer carries cooldown fields, so the restored cooldown comes from the controller snapshot unavailable_timeouts owned by the shared tracker",
    "test_legacy_spent_and_unavailable_snapshot_fields_restore_as_eligible": "operator rule: an agent is either on cooldown or not; legacy is_spent and unavailable_reason snapshot fields are ignored so no non-expiring exclusion can be restored, replacing the old Unavailable-is-not-Spent projection test",
}


def _snapshot(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "consecutive_limit": 3,
        "total_limit": 9,
        "base_cooldown_seconds": 60.0,
        "max_cooldown_seconds": 3600.0,
        "backoff_factor": 2.0,
        "entries": {f"{entry['phase']}:{entry['agent']}": entry for entry in entries},
    }


def _entry(agent: str, **updates: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "phase": "development",
        "agent": agent,
        "consecutive_unproductive": 0,
        "total_unproductive": 0,
        "cooldown_count": 0,
    }
    entry.update(updates)
    return entry


def _controller(snapshot: dict[str, object], clock: FakeClock) -> RecoveryController:
    ledger = AgentFailureLedger.from_snapshot(snapshot, clock=clock)
    return RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))


def test_all_limit_reached_agents_cool_down_with_a_finite_wait_across_projections() -> None:
    clock = FakeClock(start=10.0)
    controller = _controller(_snapshot(), clock)
    ledger = controller.ledger
    for agent in ("first", "second"):
        for _ in range(ledger.total_limit):
            ledger.record(AttemptFact("development", agent, False, workspace_changed=True))

    status = controller.agent_eligibility("development", "first")
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    ledger_wait = ledger.eligibility_wait("development", ["first", "second"])
    assert ledger_wait.kind is WaitKind.COOLING_DOWN
    assert ledger_wait.wait_ms == 60_000
    assert ledger.earliest_wait_ms("development", ["first", "second"]) == 60_000
    assert controller.eligibility_wait("development", ["first", "second"]).wait_ms == 60_000
    assert controller.agents_now_available("development", ["first", "second"]) == []
    assert controller.preferred_agent_index("development", ["first", "second"]).index is None

    clock.advance(60.0)

    assert controller.agents_now_available("development", ["first", "second"]) == [
        "first",
        "second",
    ]
    assert controller.preferred_agent_index("development", ["first", "second"]).index == 0


def test_cooling_down_has_positive_wait_and_is_not_selected() -> None:
    clock = FakeClock(start=10.0)
    controller = RecoveryController.from_snapshot(
        {
            "ledger": _snapshot(_entry("cooling")),
            "unavailable_timeouts": {"cooling": 15_000},
            "unavailability_reasons": {"cooling": "provider_unavailable"},
        },
        clock=clock,
    )

    answer = controller.agent_eligibility("development", "cooling")
    wait = controller.eligibility_wait("development", ["cooling"])

    assert isinstance(answer, Cooldown)
    assert answer.reason == "provider_unavailable"
    assert wait.kind is WaitKind.COOLING_DOWN
    assert wait.wait_ms == 5_000
    assert controller.preferred_agent_index("development", ["cooling"]).index is None


def test_eligible_has_zero_wait_and_is_selected() -> None:
    clock = FakeClock(start=10.0)
    snapshot = _snapshot()
    controller = _controller(snapshot, clock)

    answer = controller.agent_eligibility("development", "ready")
    wait = controller.eligibility_wait("development", ["ready"])

    assert isinstance(answer, Eligible)
    assert wait.kind is WaitKind.AVAILABLE_NOW
    assert wait.wait_ms == 0
    assert controller.preferred_agent_index("development", ["ready"]).index == 0


def test_legacy_spent_and_unavailable_snapshot_fields_restore_as_eligible() -> None:
    clock = FakeClock(start=10.0)
    snapshot = _snapshot(
        _entry("dead-surface", unavailable_reason="dead_tool_surface"),
        _entry("legacy-spent", is_spent=True, spent_reason="budget_exhausted"),
    )
    ledger = AgentFailureLedger.from_snapshot(snapshot, clock=clock)
    controller = _controller(snapshot, clock)

    for agent in ("dead-surface", "legacy-spent"):
        assert isinstance(ledger.status("development", agent), Eligible)
        assert isinstance(controller.agent_eligibility("development", agent), Eligible)
    wait = controller.eligibility_wait("development", ["dead-surface", "legacy-spent"])
    assert wait.kind is WaitKind.AVAILABLE_NOW
    assert not wait.has_no_candidates
    assert controller.eligibility_wait("development", []).has_no_candidates
