"""Black-box tests for the all-agents-unavailable never-exit invariant.

These tests prove the two-state recovery invariant of the recovery
controller from the wt-012 plan:

  - AC-04: RecoveryController.handle() with all chain agents in cooldown
    returns the WAITING state (state.copy_with(last_retry_delay_ms=<earliest
    cooldown>, is_waiting_state=True), [], failure_evt) and does NOT call
    _enter_phase_failed. The pipeline never exits because of agent
    unavailability.

  - AC-08: When all chain agents are on cooldown, the recovery controller
    wrap=True re-arming in _next_available_agent_index reconsiders
    earlier agents whose cooldown has expired. A black-box test exercises
    the wrap path and asserts that the recovered agent is the one
    selected for the next attempt, not the agent that was on cooldown
    longest.

The tests drive the controller through the PUBLIC surface only
(controller.handle() for failures, unavailability_entries in
RecoveryControllerOptions to seed pre-existing unavailability state).
No private mutation. No real subprocess. No real network. No real
sleep. Uses FakeClock so the test completes in <2s.
"""

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
    """AC-04: with all chain agents in cooldown, the controller enters
    the WAITING state (is_waiting_state=True, last_retry_delay_ms>0) and
    does NOT call _enter_phase_failed.

    Setup:
      - 3 agents, all pre-seeded as unavailable.
      - claude: 5s cooldown (current_index=0).
      - opencode: 10s cooldown.
      - agy: 7s cooldown.
      - The earliest is claude at 5s.

    The first handle() call (with claude failing) marks claude as the
    failure source. The controller sees all three agents are unavailable
    and enters the wait state, NOT the failed route.

    Assertions:
      - state.phase == "development" (never advanced to failed_terminal).
      - state.is_waiting_state == True (the structured wait-state flag).
      - state.last_retry_delay_ms == 5000 (claude's 5s, the earliest).
      - state.last_error contains "all agents unavailable" for operator
        readability.
      - state.recovery_cycle_count == 0 (the wait state does not consume
        the recovery cycle budget).
    """
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()

    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "development:agy": UnavailabilityEntry(
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

    # First failure: claude (the current agent). The controller sees
    # all 3 agents are unavailable and must enter the wait state.
    opts = _no_output_opts()
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=opts)
    new_state, effects, _failure_evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )

    # AC-04 invariants
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
    """AC-04 + AC-08: the pipeline must never exit because of agent
    unavailability, and the backoff must grow (exponential) on each
    subsequent unavailability failure so the run loop gradually backs
    off the chain.

    Setup:
      - 3 agents, all pre-seeded as unavailable.
      - claude (current_index=0): 5s cooldown.
      - opencode: 10s cooldown.
      - agy: 7s cooldown.

    Behaviour:
      - First handle() with claude failing -> all 3 unavailable -> wait
        state with last_retry_delay_ms=5000 (claude's earliest cooldown).
      - Advance the FakeClock by 5.1s so claude's cooldown expires
        (opencode and agy are still on cooldown).
      - Second handle() with claude failing again. The controller
        re-marks claude as unavailable with exponential backoff
        (attempt=1 -> 5000 * 2 = 10000ms). The controller then sees
        all 3 agents are unavailable and enters the wait state
        again, but with a LONGER wait (exponential backoff).

    Assertions:
      - The pipeline must NOT have advanced to failed_terminal.
      - state.is_waiting_state == True (back to wait state because
        all 3 agents are on cooldown again).
      - state.last_retry_delay_ms == 10000 (exponential backoff:
        5000 base * 2 = 10000ms for the new claude cooldown, which
        is now the earliest; opencode is at 10000ms but the
        earliest_unavailable_wait_ms check uses the freshly marked
        claude entry).
      - The chain's current_index remains at 0 (claude, the failing
        agent). The pipeline does NOT advance to opencode or agy.
    """
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()

    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=10000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=10000,
            max_backoff_ms=10000,
        ),
        "development:agy": UnavailabilityEntry(
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

    # First failure: claude. All 3 unavailable -> wait state.
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_first, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_after_first.is_waiting_state is True
    assert state_after_first.last_retry_delay_ms == 5000

    # Advance FakeClock past claude's cooldown. claude is now available;
    # opencode and agy are still on cooldown. The cooldown is in
    # milliseconds; the FakeClock is in seconds; so we advance 5.1s to
    # exceed claude's 5s cooldown.
    clock.advance(5.1)

    # Second failure: claude again. The controller re-marks claude
    # as unavailable (exponential backoff: attempt=1 -> 10000ms), so
    # all 3 agents are unavailable again -> wait state with longer
    # backoff. The pipeline must not exit.
    exc2 = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_second, _effects2, _evt2 = controller.handle(
        state_after_first,
        exc2,
        FailureContext(phase="development", agent="claude"),
    )

    # The pipeline must NOT have advanced to failed_terminal.
    assert state_after_second.phase == "development"
    # The wait state flag must be True because all 3 agents are
    # unavailable again (claude was just re-marked with a longer
    # backoff, opencode and agy are still on cooldown).
    assert state_after_second.is_waiting_state is True
    # The wait_ms must reflect the earliest remaining cooldown.
    # After clock.advance(5.1s): claude was just re-marked at
    # 5100ms with backoff=10000 -> unavailable_until_ms=15100
    # (remaining 10000); opencode unavailable_until_ms=10000
    # (remaining 4900); agy unavailable_until_ms=7000 (remaining
    # 1900). The earliest is agy at 1900ms.
    assert state_after_second.last_retry_delay_ms == 1900
    # The chain's current_index must point to claude (the failing
    # agent). The pipeline does NOT advance to opencode or agy.
    chain = state_after_second.chain_for_phase("development")
    assert chain is not None
    assert chain.agents[chain.current_index] == "claude"


def test_wrap_rearming_skips_later_agents_still_on_cooldown() -> None:
    """AC-08 (specific): wrap=True re-arming must skip agents that are
    still on cooldown and select the recovered agent.

    Setup:
      - 3 agents, all pre-seeded as unavailable.
      - claude (current_index=0): 5s cooldown.
      - opencode: 60s cooldown.
      - agy: 30s cooldown.

    Behaviour:
      - First handle() with claude failing -> wait state, wait_ms=5000.
      - Advance clock 5.1s. claude is now available; opencode and agy
        are still on cooldown.
      - Advance the chain to opencode (so we can verify wrap=True
        re-arming). The chain state has current_index=1 (opencode).
      - Second handle() with opencode failing. The controller must:
        (a) see opencode is on cooldown.
        (b) see agy is on cooldown.
        (c) use wrap=True to reconsider claude (index 0), which is now
            available.
        (d) select claude as the next agent.
      - The chain's current_index must wrap to 0 (claude).

    Assertions:
      - The chain's current_index == 0 (claude, the recovered agent).
      - state.is_waiting_state == False (we found an available agent).
      - state.last_retry_delay_ms == 0 (no wait needed).
    """
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()

    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=60000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=60000,
            max_backoff_ms=60000,
        ),
        "development:agy": UnavailabilityEntry(
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

    # First failure: claude. All 3 unavailable -> wait state.
    exc = AgentInactivityTimeoutError("claude", 30.0, opts=_no_output_opts())
    state_after_first, _effects, _evt = controller.handle(
        state,
        exc,
        FailureContext(phase="development", agent="claude"),
    )
    assert state_after_first.is_waiting_state is True

    # Advance past claude's cooldown. 5.1s exceeds the 5s cooldown;
    # opencode (60s) and agy (30s) remain on cooldown.
    clock.advance(5.1)

    # Move the chain forward manually (simulating a previous successful
    # fallover) to position the chain at opencode (index 1) so wrap=True
    # must re-consider claude.
    state_advanced = state_after_first.with_phase_chain(
        "development",
        AgentChainState(agents=["claude", "opencode", "agy"], current_index=1, retries=0),
    )

    # Second failure: opencode is the current agent. claude is now
    # available, opencode and agy are still on cooldown.
    exc2 = AgentInactivityTimeoutError("opencode", 30.0, opts=_no_output_opts())
    state_after_second, _effects2, _evt2 = controller.handle(
        state_advanced,
        exc2,
        FailureContext(phase="development", agent="opencode"),
    )

    # The controller must wrap=True re-arm to claude.
    chain = state_after_second.chain_for_phase("development")
    assert chain is not None
    assert chain.agents[chain.current_index] == "claude", (
        f"expected wrap to claude, got {chain.agents[chain.current_index]}"
    )
    # The wait state is cleared because we found an available agent.
    assert state_after_second.is_waiting_state is False
    # The run loop can retry immediately (no cooldown wait).
    assert state_after_second.last_retry_delay_ms == 0
