"""Black-box test: network loss is not counted against agent budget."""

from __future__ import annotations

from ralph.config.enums import PHASE_DEVELOPMENT
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEvent, FailureEventBus
from ralph.recovery.testing import FakeConnectivityMonitor


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=agents, current_index=0, retries=0),
    )


def test_environmental_failure_does_not_debit_budget() -> None:
    """Connection errors must not count against the agent budget."""
    collected: list[FailureEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    controller = RecoveryController(cycle_cap=10, event_bus=bus)
    state = _make_state(["claude"])

    new_state, effects, evt = controller.handle(
        state,
        ConnectionError("connection reset by peer"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert new_state.phase == PHASE_DEVELOPMENT
    assert effects == []
    assert evt.counted_against_budget is False
    assert evt.category == "environmental"
    assert new_state.recovery_cycle_count == 0
    assert new_state.last_failure_category == "environmental"


def test_environmental_failure_via_message_substring() -> None:
    """Failures with transport substrings in the message are classified environmental."""
    controller = RecoveryController(cycle_cap=10)
    state = _make_state(["claude"])

    new_state, effects, evt = controller.handle(
        state,
        "ECONNREFUSED: connection refused to 127.0.0.1:8080",
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert evt.category == "environmental"
    assert evt.counted_against_budget is False
    assert new_state.phase == PHASE_DEVELOPMENT
    assert effects == []


def test_environmental_failure_via_reducer_with_controller() -> None:
    """PhaseFailureEvent with env error routes via controller, no budget debit."""
    controller = RecoveryController(cycle_cap=10)
    state = _make_state(["claude"])

    event = PhaseFailureEvent(
        phase=PHASE_DEVELOPMENT,
        reason="connection reset by peer",
        recoverable=True,
    )

    new_state, _ = reduce(state, event, None, recovery=controller)

    assert new_state.phase == PHASE_DEVELOPMENT


def test_connectivity_monitor_state_transitions() -> None:
    """FakeConnectivityMonitor emits state transitions correctly."""
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    transitions: list[ConnectivityState] = []

    monitor.add_listener(lambda evt: transitions.append(evt.state))

    assert monitor.current_state == ConnectivityState.ONLINE

    monitor.go_offline("test network down")
    assert monitor.current_state == ConnectivityState.OFFLINE
    assert ConnectivityState.OFFLINE in transitions

    monitor.go_online("test network restored")
    assert monitor.current_state == ConnectivityState.ONLINE
    assert ConnectivityState.ONLINE in transitions


def test_environmental_failure_no_fallover_record() -> None:
    """Environmental failures must not produce fallover records."""
    controller = RecoveryController(cycle_cap=10)
    state = _make_state(["claude", "opencode"])

    controller.handle(
        state,
        ConnectionError("Temporary failure in name resolution"),
        phase=PHASE_DEVELOPMENT,
        agent="claude",
    )

    assert len(state.fallover_history) == 0
