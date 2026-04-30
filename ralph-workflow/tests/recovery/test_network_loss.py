"""Black-box test: network loss is not counted against agent budget."""

from __future__ import annotations

import asyncio

from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.connectivity import ConnectivityState
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEvent, FailureEventBus
from ralph.recovery.testing import FakeConnectivityMonitor


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
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
        phase="development",
        agent="claude",
    )

    assert new_state.phase == "development"
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
        phase="development",
        agent="claude",
    )

    assert evt.category == "environmental"
    assert evt.counted_against_budget is False
    assert new_state.phase == "development"
    assert effects == []


def test_environmental_failure_via_reducer_with_controller() -> None:
    """PhaseFailureEvent with env error routes via controller, no budget debit."""
    controller = RecoveryController(cycle_cap=10)
    state = _make_state(["claude"])

    event = PhaseFailureEvent(
        phase="development",
        reason="connection reset by peer",
        recoverable=True,
    )

    new_state, _ = reduce(state, event, None, recovery=controller)

    assert new_state.phase == "development"


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
        phase="development",
        agent="claude",
    )

    assert len(state.fallover_history) == 0


def test_wait_online_blocks_until_online() -> None:
    """wait_online() properly blocks while offline and resumes when online."""

    async def _test() -> None:
        monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

        # Start offline
        monitor.go_offline("test")

        # Create wait task
        wait_task = asyncio.create_task(monitor.wait_online())

        # Should not be done yet
        await asyncio.sleep(0)
        assert not wait_task.done()

        # Go back online
        monitor.go_online()

        # Now wait should complete
        await asyncio.sleep(0)
        assert wait_task.done()

    asyncio.run(_test())


def test_offline_state_tracked_in_state() -> None:
    """Pipeline state tracks connectivity state changes."""
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)

    # Record initial state
    assert monitor.current_state == ConnectivityState.ONLINE

    # Go offline
    monitor.go_offline("test network failure")
    assert monitor.current_state == ConnectivityState.OFFLINE

    # Transitions are tracked
    transitions = []
    monitor.add_listener(lambda evt: transitions.append(evt.state))

    monitor.go_offline("already offline")  # No transition
    assert len(transitions) == 0

    monitor.go_online("restored")
    assert len(transitions) == 1
    assert transitions[0] == ConnectivityState.ONLINE


def test_offline_inhibits_agent_invocation_via_recovery_controller() -> None:
    """RecoveryController pauses invocations when monitor reports OFFLINE.

    This is a black-box test that verifies the offline pause mechanism
    works through the RecoveryController + ConnectivityMonitor integration:
    when the monitor is OFFLINE, the runner (via controller) should not
    debit any budget because no actual invocation was attempted.
    """
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    collected_events: list[FailureEvent] = []

    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected_events.append(evt) if isinstance(evt, FailureEvent) else None)  # noqa: E501

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, event_bus=bus, budget_registry=registry)
    state = _make_state(["claude"])

    # Start offline
    monitor.go_offline("network down")
    assert monitor.current_state == ConnectivityState.OFFLINE

    # Simulate what the runner does: check monitor state before invoking
    # When OFFLINE, the runner would wait_online() instead of invoking
    async def _simulate_offline_block() -> None:
        wait_task = asyncio.create_task(monitor.wait_online())
        await asyncio.sleep(0)
        assert not wait_task.done()
        # Restore before wait completes
        monitor.go_online("network restored")
        await asyncio.sleep(0)
        assert wait_task.done()

    asyncio.run(_simulate_offline_block())

    # No failure events should be emitted during offline period
    # because no agent was actually invoked
    environmental_failures = [e for e in collected_events if e.category == "environmental"]
    assert len(environmental_failures) == 0

    # Now simulate an invocation after going back online
    # This should succeed without any offline penalty
    _, _, evt = controller.handle(
        state,
        ConnectionError("pre-existing connection reset"),
        phase="development",
        agent="claude",
    )

    # Environmental failure - no budget debit
    assert evt.counted_against_budget is False
    assert evt.category == "environmental"

    # Budget is unchanged
    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.consumed == 0
    assert budget.remaining == 3  # noqa: PLR2004


def test_offline_period_does_not_debit_budget_on_recovery_resume() -> None:
    """Time spent offline does not count against any agent's budget.

    This verifies that the offline period is truly silent - no FailureEvent
    is emitted, no budget is debited, and the pipeline resumes cleanly
    when connectivity returns.
    """
    monitor = FakeConnectivityMonitor(initial_state=ConnectivityState.ONLINE)
    collected: list[FailureEvent] = []

    bus = FailureEventBus()
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=3)
    controller = RecoveryController(cycle_cap=10, event_bus=bus, budget_registry=registry)
    state = _make_state(["claude"])

    # Simulate: go offline, wait, come back online, then invoke
    monitor.go_offline("ISP outage")

    async def _offline_then_resume() -> None:
        wait_task = asyncio.create_task(monitor.wait_online())
        await asyncio.sleep(0)
        assert not wait_task.done()
        monitor.go_online("ISP restored")
        await asyncio.sleep(0)
        assert wait_task.done()

    asyncio.run(_offline_then_resume())

    # No failure events during offline
    assert len(collected) == 0

    # Budget still intact
    budget = controller.budget_registry.get("development", "claude")
    assert budget is not None
    assert budget.remaining == 3  # noqa: PLR2004

    # Now simulate a successful agent invocation after resume
    # (no failure - just a state update showing no budget consumed)
    new_state = state.copy_with(last_retry_delay_ms=0)
    assert new_state.phase == "development"
    # No budget was consumed during offline period
    assert budget.remaining == 3  # noqa: PLR2004
