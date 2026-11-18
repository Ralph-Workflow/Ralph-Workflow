from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    LEDGER_VERSION,
    AgentFailureLedger,
    AttemptFact,
    CoolingDown,
    Eligible,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_agent_eligibility_returns_each_closed_status": "operator rule: an agent is either on cooldown or not; the Spent and Unavailable statuses no longer exist, so the test asserts the two remaining statuses and that the total-limit and confirmed-unavailability paths both yield an expiring Cooldown",
    "test_total_limit_chain_has_a_finite_eligibility_wait": "operator rule: an agent is either on cooldown or not; the total unproductive limit now starts an expiring unproductive_limit cooldown instead of a permanent Spent state, so the chain has a finite wait and the agent is selectable after it expires",
}


def test_agent_eligibility_returns_each_closed_status() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=3)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock), ledger=ledger)

    assert isinstance(ledger.agent_eligibility("development", "eligible"), Eligible)
    ledger.record(AttemptFact("development", "cooling", False))
    ledger.record(AttemptFact("development", "cooling", False))
    assert isinstance(ledger.agent_eligibility("development", "cooling"), CoolingDown)
    for _ in range(3):
        ledger.record(AttemptFact("development", "total", False, workspace_changed=True))
    total_status = ledger.agent_eligibility("development", "total")
    assert isinstance(total_status, CoolingDown)
    assert total_status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    controller.mark_agent_unavailable(
        "development", "offline", reason=UnavailabilityReason.PROVIDER_UNAVAILABLE
    )
    offline_status = ledger.agent_eligibility("development", "offline")
    assert isinstance(offline_status, CoolingDown)
    assert offline_status.until_ms > 0


def test_controller_selection_and_wait_are_projections_of_eligibility() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2)
    ledger.record(AttemptFact("development", "primary", False))
    ledger.record(AttemptFact("development", "primary", False))
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock),
        ledger=ledger,
    )

    statuses = dict(controller.chain_eligibility("development", ("primary", "fallback")))
    selection = controller.preferred_agent_index("development", ("primary", "fallback"))

    assert isinstance(statuses["primary"], CoolingDown)
    assert isinstance(statuses["fallback"], Eligible)
    assert controller.agents_now_available("development", ["primary", "fallback"]) == ["fallback"]
    assert controller.earliest_eligible_wait_ms("development", ("primary", "fallback")) == 0
    assert selection.agent == "fallback"


def test_total_limit_chain_has_a_finite_eligibility_wait() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(AttemptFact("development", "tired", False, workspace_changed=True))
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock),
        ledger=ledger,
    )

    status = controller.agent_eligibility("development", "tired")
    assert isinstance(status, CoolingDown)
    wait_ms = controller.earliest_eligible_wait_ms("development", ("tired",))
    assert wait_ms == status.until_ms
    assert wait_ms is not None
    assert wait_ms > 0
    assert controller.agents_now_available("development", ["tired"]) == []

    clock.advance(wait_ms / 1000.0)

    assert isinstance(controller.agent_eligibility("development", "tired"), Eligible)
    assert controller.agents_now_available("development", ["tired"]) == ["tired"]


def test_ledger_version_drift_rebuilds_and_emits_one_notice() -> None:
    notices: list[str] = []
    restored = AgentFailureLedger.from_snapshot(
        {"version": LEDGER_VERSION + 1, "entries": {"development:agent": {"is_spent": True}}},
        clock=FakeClock(start=0.0),
        notice=notices.append,
    )

    assert isinstance(restored.agent_eligibility("development", "agent"), Eligible)
    assert len(notices) == 1
    assert "rebuilt" in notices[0]
