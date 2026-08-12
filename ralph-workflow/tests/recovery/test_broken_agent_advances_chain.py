"""End-to-end recovery regression for broken-agent fallover."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke import BrokenAgentExitError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _minimal_policy_bundle() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent")


def test_recovery_regression_broken_agent_advances_chain_without_same_agent_retry() -> None:
    """S-9: broken agents fall over immediately rather than consuming retries."""
    clock = FakeClock(start=0.0)
    events: list[FailureEvent | FalloverEvent] = []
    event_bus = FailureEventBus()
    event_bus.subscribe(events.append)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=event_bus,
        )
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["agent_a", "agent_b"], current_index=0),
        },
    ).copy_with(last_connectivity_state="online")

    next_state, effects, _failure_event = controller.handle(
        state,
        BrokenAgentExitError("agent_a", reason="no_output"),
        FailureContext(phase="development", agent="agent_a"),
    )

    fallover_events = [event for event in events if isinstance(event, FalloverEvent)]
    assert len(fallover_events) == 1
    fallover = fallover_events[0]
    assert fallover.from_agent == "agent_a"
    assert fallover.to_agent == "agent_b"
    assert fallover.unavailability_reason == UnavailabilityReason.BROKEN_AGENT
    assert effects == []
    assert controller.unavailability_store.is_available("development", "agent_a") is False
    chain = next_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert chain.retries == 0
