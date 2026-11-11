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
from ralph.recovery.unavailability_reason import UnavailabilityReason


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


def test_handle_with_all_agents_unavailable_enters_wait_state_not_failed() -> None:
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
        "agy": UnavailabilityEntry(
            unavailable_until_ms=7000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=7000,
            max_backoff_ms=7000,
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

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)
    new_state, effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    assert new_state.phase == "development", (
        f"phase must not advance to failed_terminal, got {new_state.phase}"
    )
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms == 5000, (
        f"expected earliest cooldown (5000ms), got {new_state.last_retry_delay_ms}"
    )
    assert new_state.last_error is not None
    assert "all agents unavailable" in new_state.last_error
    assert new_state.recovery_cycle_count == 0
    assert effects == []


def test_handle_after_earliest_cooldown_expires_does_not_exit_pipeline() -> None:
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
        "agy": UnavailabilityEntry(
            unavailable_until_ms=7000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=7000,
            max_backoff_ms=7000,
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

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_first, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_after_first.is_waiting_state is True
    assert state_after_first.last_retry_delay_ms == 5000

    clock.advance(5.1)

    exc2 = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_second, _effects2, _evt2 = controller.handle(
        state_after_first,
        exc2,
        FailureContext(phase="development", agent="claude"),
    )

    assert state_after_second.phase == "development"
    assert state_after_second.is_waiting_state is True
    assert state_after_second.last_retry_delay_ms == 1900
    chain = state_after_second.chain_for_phase("development")
    assert chain is not None
    assert chain.agents[chain.current_index] == "claude"


def test_wrap_rearming_skips_later_agents_still_on_cooldown() -> None:
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
            unavailable_until_ms=60000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=60000,
            max_backoff_ms=60000,
        ),
        "agy": UnavailabilityEntry(
            unavailable_until_ms=30000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=30000,
            max_backoff_ms=30000,
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

    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_first, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_after_first.is_waiting_state is True

    clock.advance(5.1)

    state_advanced = state_after_first.with_phase_chain(
        "development",
        AgentChainState(agents=["claude", "opencode", "agy"], current_index=1, retries=0),
    )

    exc2 = AgentInactivityTimeoutError("opencode", 30.0, opts=_no_output_opts())
    state_after_second, _effects2, _evt2 = controller.handle(
        state_advanced,
        exc2,
        FailureContext(phase="development", agent="opencode"),
    )

    chain = state_after_second.chain_for_phase("development")
    assert chain is not None
    assert chain.agents[chain.current_index] == "claude", (
        f"expected wrap to claude, got {chain.agents[chain.current_index]}"
    )
    assert state_after_second.is_waiting_state is False
    assert state_after_second.last_retry_delay_ms == 0
