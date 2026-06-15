from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def test_wait_state_survives_ten_cooldown_cycles() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    policy = {
        UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
            base_backoff_ms=1000, max_backoff_ms=1005
        )
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            unavailability_backoff_policy=policy,
        )
    )
    chain_state = AgentChainState(agents=["claude", "opencode", "agy"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)

    last_delays = []
    for _ in range(30):
        # Mark all agents unavailable before the handle call
        for agent in ["claude", "opencode", "agy"]:
            controller._unavailability_tracker.mark_unavailable(
                "development", agent, UnavailabilityReason.NO_OUTPUT_AT_START
            )
        clock.advance(0.5)
        chain = state.chain_for_phase("development")
        current_agent = chain.agents[chain.current_index] if chain is not None else "claude"
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=current_agent),
        )
        assert state.phase == "development"
        assert state.last_retry_delay_ms > 0
        last_delays.append(state.last_retry_delay_ms)
        assert state.recovery_cycle_count == 0

    for i in range(len(last_delays) - 1):
        assert last_delays[i] <= last_delays[i + 1]


def test_wait_state_resumes_to_first_available_agent_after_cooldown() -> None:
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
    chain_state = AgentChainState(agents=["claude", "opencode", "agy"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    # Mark all unavailable with different cooldowns
    # claude: 5s, opencode: 2s, agy: 10s
    tracker = controller._unavailability_tracker

    # We construct UnavailabilityEntry directly to control exact timeouts
    tracker._entries["development:claude"] = UnavailabilityEntry(
        unavailable_until_ms=5000,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )
    tracker._entries["development:opencode"] = UnavailabilityEntry(
        unavailable_until_ms=2000,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=2000,
        max_backoff_ms=2000,
    )
    tracker._entries["development:agy"] = UnavailabilityEntry(
        unavailable_until_ms=10000,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=10000,
        max_backoff_ms=10000,
    )

    # Advance clock to 3s -> opencode is now available, claude and agy are not.
    clock.advance(3.0)

    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)

    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    # Should fall over to opencode (index 1)
    chain = state.chain_for_phase("development")
    assert chain.current_index == 1
    assert state.last_retry_delay_ms == 0


def test_wait_state_first_cooldown_uses_earliest_unavailable_wait() -> None:
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

    # Mark both unavailable: claude for 5s, opencode for 10s
    tracker = controller._unavailability_tracker
    tracker._entries["development:claude"] = UnavailabilityEntry(
        unavailable_until_ms=5000,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )
    tracker._entries["development:opencode"] = UnavailabilityEntry(
        unavailable_until_ms=10000,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=10000,
        max_backoff_ms=10000,
    )

    opts = InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)

    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    # Should wait for the earliest (claude: 5000ms)
    assert state.last_retry_delay_ms == 5000
