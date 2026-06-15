from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def test_out_of_credits_message_classifies_as_unavailable_with_per_reason_backoff() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
        )
    )
    chain_state = AgentChainState(agents=["claude", "opencode"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    exc = AgentInvocationError("claude", 1, "You've hit your weekly limit")

    state, _effects, failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert failure_evt.category == "agent"
    assert failure_evt.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS.value
    # Fast fallover to opencode (index 1)
    chain = state.chain_for_phase("development")
    assert chain.current_index == 1
    # opencode is available
    assert controller._unavailability_tracker.is_available("development", "opencode") is True
    # claude has the OUT_OF_CREDITS base timeout (60_000ms)
    snapshot = controller.snapshot()
    assert snapshot["unavailable_timeouts"]["development:claude"] == 60_000


def test_out_of_credits_backoff_doubles_each_retry_up_to_thirty_minute_cap() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
        )
    )
    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    exc = AgentInvocationError("claude", 1, "You've hit your weekly limit")

    expected_cooldowns = [60_000, 120_000, 240_000, 480_000, 960_000, 1_800_000]

    for i in range(6):
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="claude"),
        )
        snapshot = controller.snapshot()
        current_time_ms = int(clock.monotonic() * 1000)
        cooldown = snapshot["unavailable_timeouts"]["development:claude"] - current_time_ms
        assert cooldown == expected_cooldowns[i]

        # Advance the clock past the cooldown so it can be marked again
        clock.advance(expected_cooldowns[i] / 1000.0)
