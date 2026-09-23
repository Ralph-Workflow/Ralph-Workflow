"""Regression tests for priority agent selection in RecoveryController."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 1) -> PipelineState:
    chain_state = AgentChainState(
        agents=["claude", "opencode", "agy"],
        current_index=current_index,
        retries=0,
    )
    return PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")


def test_return_to_preferred_agent_after_cooldown_expiry() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    clock.advance(6.0)

    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_priority_beats_proximity() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_priority_beats_same_agent_retry() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


def test_cooldown_is_never_picked() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
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
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index != 0


def test_all_in_cooldown_still_waits() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=8000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=8000,
            max_backoff_ms=8000,
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
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=2)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("agy", 30.0, opts=opts)
    new_state, effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="agy"),
    )
    assert new_state.is_waiting_state is True
    assert new_state.last_retry_delay_ms == 5000
    assert new_state.phase == "development"
    assert effects == []


def test_transcript_visibility() -> None:
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    logs: list[str] = []

    def sink(msg: Any) -> None:
        logs.append(str(msg))

    sink_id = logger.add(sink, level="INFO", format="{message}")
    try:
        opts = _no_output_opts()
        exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
        _new_state, _effects, _evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="opencode"),
        )
    finally:
        logger.remove(sink_id)

    assert any("Selected agent" in line for line in logs)
    assert any("cooldown (5000ms remaining, reason=no_output_at_start)" in line for line in logs)


def test_non_unavailable_failures_re_prefer() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    exc = RuntimeError("generic failure")
    new_state, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="opencode"),
    )
    chain = new_state.chain_for_phase(new_state.phase)
    assert chain is not None
    assert chain.current_index == 0


# ---------------------------------------------------------------------------
# Invocation-boundary regression coverage (plan S-2)
#
# These tests assert on observable selection state across TWO consecutive
# invocation boundaries: priority order is the source of truth on every
# call, NOT a chain cursor carried from the prior selection.
# ---------------------------------------------------------------------------


def test_two_consecutive_invocations_keep_priority_selection() -> None:
    """Two consecutive handle() calls both pick the highest-priority available agent.

    First call: opencode (index 1) fails -> selection falls back to
    claude (index 0) because claude is the highest-priority available agent.

    Second call: opencode (index 1) is re-attempted and fails again
    after the next observation. The selection MUST return to claude
    (index 0) once more -- not advance to index 2 -- because claude
    remains the highest-priority available agent. The chain cursor
    is not allowed to drift forward across invocations.
    """
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0
    assert chain.agents[chain.current_index] == "claude"

    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_cooldown_then_expiry_restores_highest_priority_agent() -> None:
    """Across invocation boundaries: a cooldown picks the next available agent;

    expiry of the higher-priority agent's cooldown MUST restore that
    agent on the subsequent invocation. Priority is the source of truth;
    the cursor from the previous invocation is not.
    """
    clock = FakeClock(start=0.0)
    initial_entries = {
        "claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
    }
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    # Use a generic (non-unavailable) failure so opencode stays available.
    # This isolates the test to the cooldown-first selection rule: claude is
    # on cooldown, so the highest-priority available agent is opencode
    # (index 1), which happens to match the cursor. The selection still
    # routes through ``preferred_agent_index`` and picks opencode
    # independently of the cursor.
    exc = RuntimeError("generic failure")
    ctx = FailureContext(phase="development", agent="opencode")

    # First invocation: claude on cooldown, opencode is the highest-priority
    # available agent.
    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 1
    assert chain.agents[chain.current_index] == "opencode"

    # Expire claude's cooldown and force the controller to re-select.
    clock.advance(6.0)
    state_after_expiry = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_expiry.chain_for_phase(state_after_expiry.phase)
    assert chain2 is not None
    # claude's cooldown expired, so claude is back as the highest-priority
    # available agent -- the selection returns to index 0 even though
    # the previous invocation was at index 1.
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_successful_preferred_agent_remains_preferred_on_later_invocations() -> None:
    """When the preferred (highest-priority available) agent succeeds, a later

    invocation MUST still pick it -- priority is not a "consume-and-advance"
    cursor. AC-04: success clears the unavailable state, so the agent
    remains the priority choice on the next call.
    """
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    # First invocation: opencode fails, falls back to claude (preferred).
    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0

    # Production success path: runner resets backoff after a successful
    # invocation. Mirror that here.
    controller.reset_backoff("development", "claude")

    # Next invocation: claude remains the highest-priority available agent.
    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"


def test_invocation_boundary_skips_exhausted_agent_returns_to_preferred() -> None:
    """Cross-invocation: priority picks the highest-priority selectable agent

    each time even when an intermediate agent was previously selected.
    This guards against the round-robin-from-cursor regression the
    plan forbids.
    """
    clock = FakeClock(start=0.0)
    initial_entries = {
        "opencode": UnavailabilityEntry(
            unavailable_until_ms=4000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=4000,
            max_backoff_ms=4000,
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
            unavailability_entries=initial_entries,
        )
    )
    state = _three_agent_state(current_index=1)
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("opencode", 30.0, opts=opts)
    ctx = FailureContext(phase="development", agent="opencode")

    # claude is the only available agent (highest priority). Selection
    # returns to claude (index 0) -- not to agy (index 2) -- even though
    # the cursor was at index 1.
    state_after_first = controller.handle(state, exc, ctx)[0]
    chain = state_after_first.chain_for_phase(state_after_first.phase)
    assert chain is not None
    assert chain.current_index == 0
    assert chain.agents[chain.current_index] == "claude"

    # Advance the clock so opencode's cooldown expires; agy's stays.
    clock.advance(5.0)
    state_after_second = controller.handle(state_after_first, exc, ctx)[0]
    chain2 = state_after_second.chain_for_phase(state_after_second.phase)
    assert chain2 is not None
    # claude still available and still highest priority, even though
    # opencode's cooldown has now expired.
    assert chain2.current_index == 0
    assert chain2.agents[chain2.current_index] == "claude"
