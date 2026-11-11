from __future__ import annotations

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FailureEventBus
from ralph.recovery.testing import FakeConnectivityMonitor


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def test_environmental_failure_does_not_debit_budget() -> None:
    collected: list[FailureEvent] = []
    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))
    state = _make_state(["claude"])

    new_state, effects, evt = controller.handle(
        state,
        ConnectionError("connection reset by peer"),
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development"
    assert effects == []
    assert evt.counted_against_budget is False
    assert evt.category == "environmental"
    assert new_state.recovery_cycle_count == 0
    assert new_state.last_failure_category == "environmental"


def test_environmental_failure_via_message_substring() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    new_state, effects, evt = controller.handle(
        state,
        "ECONNREFUSED: connection refused to 127.0.0.1:8080",
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.category == "environmental"
    assert evt.counted_against_budget is False
    assert new_state.phase == "development"
    assert effects == []


def test_environmental_failure_via_reducer_with_controller() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    event = PhaseFailureEvent(
        phase="development",
        reason="connection reset by peer",
        recoverable=True,
    )

    new_state, _ = reduce(state, event, None, recovery=controller)

    assert new_state.phase == "development"


def test_connectivity_monitor_state_transitions() -> None:
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
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude", "opencode"])

    controller.handle(
        state,
        ConnectionError("Temporary failure in name resolution"),
        FailureContext(phase="development", agent="claude"),
    )

    assert len(state.fallover_history) == 0


def test_offline_monitor_resumes_online_state() -> None:
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    monitor.go_offline("test")
    assert monitor.current_state == ConnectivityState.OFFLINE

    monitor.go_online()
    assert monitor.current_state == ConnectivityState.ONLINE


def test_offline_state_tracked_in_state() -> None:
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    assert monitor.current_state == ConnectivityState.ONLINE

    monitor.go_offline("test network failure")
    assert monitor.current_state == ConnectivityState.OFFLINE

    transitions = []
    monitor.add_listener(lambda evt: transitions.append(evt.state))

    monitor.go_offline("already offline")
    assert len(transitions) == 0

    monitor.go_online("restored")
    assert len(transitions) == 1
    assert transitions[0] == ConnectivityState.ONLINE


def test_offline_inhibits_agent_invocation_via_recovery_controller() -> None:
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    collected_events: list[FailureEvent] = []

    bus = FailureEventBus()
    bus.subscribe(
        lambda evt: collected_events.append(evt) if isinstance(evt, FailureEvent) else None
    )

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))
    state = _make_state(["claude"])

    monitor.go_offline("network down")
    assert monitor.current_state == ConnectivityState.OFFLINE

    monitor.go_online("network restored")
    assert monitor.current_state == ConnectivityState.ONLINE

    environmental_failures = [e for e in collected_events if e.category == "environmental"]
    assert len(environmental_failures) == 0

    _, _, evt = controller.handle(
        state,
        ConnectionError("pre-existing connection reset"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.counted_against_budget is False
    assert evt.category == "environmental"

    assert controller.ledger.is_available("development", "claude") is True


def test_offline_period_does_not_debit_budget_on_recovery_resume() -> None:
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    collected: list[FailureEvent] = []

    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))
    state = _make_state(["claude"])

    monitor.go_offline("ISP outage")

    assert monitor.current_state == ConnectivityState.OFFLINE
    monitor.go_online("ISP restored")
    assert monitor.current_state == ConnectivityState.ONLINE

    assert len(collected) == 0

    assert controller.ledger.is_available("development", "claude") is True

    new_state = state.copy_with(last_retry_delay_ms=0)
    assert new_state.phase == "development"
    assert controller.ledger.is_available("development", "claude") is True
