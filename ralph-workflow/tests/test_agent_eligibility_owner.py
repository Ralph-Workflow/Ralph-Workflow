from __future__ import annotations

from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.agent_failure_ledger import (
    AgentFailureLedger,
    Cooldown,
    Eligible,
    Spent,
    Unavailable,
    WaitKind,
    project_eligibility,
)
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions


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
        "cooldown_until_ms": None,
        "cooldown_reason": None,
        "cooldown_count": 0,
        "is_spent": False,
        "spent_reason": None,
        "unavailable_reason": None,
    }
    entry.update(updates)
    return entry


def _controller(snapshot: dict[str, object], clock: FakeClock) -> RecoveryController:
    ledger = AgentFailureLedger.from_snapshot(snapshot, clock=clock)
    return RecoveryController(options=RecoveryControllerOptions(clock=clock, ledger=ledger))


def test_all_spent_is_never_eligible_across_projections() -> None:
    clock = FakeClock(start=10.0)
    snapshot = _snapshot(
        _entry("first", is_spent=True, spent_reason="budget_exhausted"),
        _entry("second", is_spent=True, spent_reason="budget_exhausted"),
    )
    ledger = AgentFailureLedger.from_snapshot(snapshot, clock=clock)
    controller = _controller(snapshot, clock)

    assert isinstance(
        project_eligibility(snapshot, phase="development", agent="first", clock=clock), Spent
    )
    assert (
        ledger.eligibility_wait("development", ["first", "second"]).kind is WaitKind.NEVER_ELIGIBLE
    )
    assert ledger.earliest_wait_ms("development", ["first", "second"]) is None
    assert controller.eligibility_wait("development", ["first", "second"]).is_never_eligible
    assert controller.agents_now_available("development", ["first", "second"]) == []
    assert controller.preferred_agent_index("development", ["first", "second"]).index is None


def test_cooling_down_has_positive_wait_and_is_not_selected() -> None:
    clock = FakeClock(start=10.0)
    snapshot = _snapshot(
        _entry(
            "cooling",
            cooldown_until_ms=15_000,
            cooldown_reason="provider_unavailable",
        )
    )
    controller = _controller(snapshot, clock)

    answer = controller.agent_eligibility("development", "cooling")
    wait = controller.eligibility_wait("development", ["cooling"])

    assert isinstance(answer, Cooldown)
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


def test_unavailable_without_cooldown_is_not_spent() -> None:
    clock = FakeClock(start=10.0)
    snapshot = _snapshot(_entry("dead-surface", unavailable_reason="dead_tool_surface"))
    ledger = AgentFailureLedger.from_snapshot(snapshot, clock=clock)
    controller = _controller(snapshot, clock)

    projected = project_eligibility(
        snapshot,
        phase="development",
        agent="dead-surface",
        clock=clock,
    )

    assert isinstance(projected, Unavailable)
    assert not projected.is_spent
    assert isinstance(ledger.status("development", "dead-surface"), Unavailable)
    assert isinstance(controller.agent_eligibility("development", "dead-surface"), Unavailable)
    assert controller.eligibility_wait("development", ["dead-surface"]).is_never_eligible
