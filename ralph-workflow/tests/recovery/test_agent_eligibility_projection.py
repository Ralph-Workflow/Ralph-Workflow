from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    LEDGER_VERSION,
    AgentFailureLedger,
    AttemptFact,
    CoolingDown,
    Eligible,
    Spent,
    Unavailable,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


def test_agent_eligibility_returns_each_closed_status() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=2, total_limit=3)

    assert isinstance(ledger.agent_eligibility("development", "eligible"), Eligible)
    ledger.record(AttemptFact("development", "cooling", False))
    ledger.record(AttemptFact("development", "cooling", False))
    assert isinstance(ledger.agent_eligibility("development", "cooling"), CoolingDown)
    for _ in range(3):
        ledger.record(AttemptFact("development", "spent", False, workspace_changed=True))
    assert isinstance(ledger.agent_eligibility("development", "spent"), Spent)
    ledger.mark_unavailable("development", "offline", reason="provider unreachable")
    assert isinstance(ledger.agent_eligibility("development", "offline"), Unavailable)


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


def test_spent_chain_has_no_finite_eligibility_wait() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, total_limit=1)
    ledger.record(AttemptFact("development", "spent", False, workspace_changed=True))
    controller = RecoveryController(
        options=RecoveryControllerOptions(clock=clock),
        ledger=ledger,
    )

    assert isinstance(controller.agent_eligibility("development", "spent"), Spent)
    assert controller.earliest_eligible_wait_ms("development", ("spent",)) is None
    assert controller.agents_now_available("development", ["spent"]) == []


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
