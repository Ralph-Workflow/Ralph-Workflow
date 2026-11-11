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


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 0) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def test_wait_state_survives_ten_cooldown_cycles() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    policy = {
        UnavailabilityReason.NO_OUTPUT_AT_START: ReasonBackoffPolicy(
            base_backoff_ms=100_000,
            max_backoff_ms=200_000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            unavailability_backoff_policy=policy,
        ),
    )
    state = _three_agent_state()
    opts = _no_output_opts()

    for agent_name in ("claude", "opencode", "agy"):
        exc = AgentInactivityTimeoutError(agent_name, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=agent_name),
        )

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == len(chain.agents) - 1
    assert state.last_retry_delay_ms > 0
    assert state.recovery_cycle_count == 0

    observed_delays_ms: list[int] = []
    initial_max_delay_ms = state.last_retry_delay_ms

    for _ in range(30):
        clock.advance(1.0)

        current_agent = chain.agents[chain.current_index]
        exc = AgentInactivityTimeoutError(current_agent, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=current_agent),
        )

        assert state.phase == "development"
        assert state.last_retry_delay_ms > 0
        assert state.recovery_cycle_count == 0
        assert state.last_retry_delay_ms <= 200_000
        observed_delays_ms.append(state.last_retry_delay_ms)

    assert state.phase == "development"
    assert state.recovery_cycle_count == 0
    assert len(observed_delays_ms) == 30

    for i, delay in enumerate(observed_delays_ms):
        assert delay > 0, f"iteration {i}: wait state delay must be positive, got {delay}ms"
        assert delay <= 200_000, (
            f"iteration {i}: wait state delay must be bounded by max_backoff_ms, got {delay}ms"
        )
    assert all(
        delay <= initial_max_delay_ms + (i + 1) * 1_000
        for i, delay in enumerate(observed_delays_ms)
    ), "wait state delay must remain stable across 30 cooldown cycles"


def test_wait_state_resumes_to_first_available_agent_after_cooldown() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()

    initial_entries: dict[str, UnavailabilityEntry] = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=2000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=2000,
            max_backoff_ms=2000,
        ),
        "agy": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            unavailability_entries=initial_entries,
        ),
    )
    state = _three_agent_state()

    clock.advance(3.0)

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert state.last_retry_delay_ms == 0


def test_wait_state_first_cooldown_uses_earliest_unavailable_wait() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()

    initial_entries: dict[str, UnavailabilityEntry] = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            unavailability_entries=initial_entries,
        ),
    )
    state = _three_agent_state(current_index=0)
    state = state.with_phase_chain(
        "development",
        AgentChainState(agents=["claude", "opencode"], current_index=0, retries=0),
    )

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)
    state, _effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert state.last_retry_delay_ms == 5000
    assert state.last_error is not None
    assert "all agents unavailable" in state.last_error
