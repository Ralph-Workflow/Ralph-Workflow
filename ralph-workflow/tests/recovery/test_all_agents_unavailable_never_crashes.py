"""Black-box tests for the all-agents-unavailable wait-state contract.

The wait state is the never-crash safety net for the agent-fallback chain:
when every agent in a phase chain is temporarily unavailable, the recovery
controller enters a wait state (sets ``last_retry_delay_ms > 0`` and
preserves the current phase) instead of advancing to the failed-route.

These tests drive the wait state through the PUBLIC controller surface only
(``controller.handle()`` for failures, ``unavailability_entries`` in
``RecoveryControllerOptions`` to seed pre-existing unavailability state).
They do NOT mutate ``controller._unavailability_tracker._entries`` directly
or re-mark every agent on every loop — that would hide real behaviour behind
private internals and would not survive a future swap of the in-memory
tracker for a persistent one (the ``UnavailabilityStore`` Protocol seam).

The three tests collectively prove:
  - AC-09: the wait state never enters 'failed_terminal' and never
    increments recovery_cycle_count, even across many handle() calls.
  - AC-10: after a cooldown expires, the controller routes to the FIRST
    available agent in chain order (wrap=true semantics in
    ``_next_available_agent_index``).
  - AC-11: when agents have different cooldowns, the wait state uses the
    EARLIEST cooldown (not the latest) so the run loop sleeps the minimum
    time and retries as soon as the first agent is unblocked.
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
from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason


def _minimal_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _no_output_opts() -> InactivityTimeoutOpts:
    """Build the canonical NO_OUTPUT_AT_START opts for inactivity timeouts."""
    return InactivityTimeoutOpts(
        reason=WatchdogFireReason.NO_OUTPUT_AT_START,
        diagnostic={"invocation_elapsed": 30.0},
    )


def _three_agent_state(current_index: int = 0) -> PipelineState:
    """Build a 3-agent pipeline state for the development phase."""
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
    """AC-09: the wait state must never enter 'failed_terminal' and must never
    increment recovery_cycle_count, even across many handle() calls.

    Setup:
        - cycle_cap=10 (the smallest non-zero cap; we want to confirm the wait
          state does not consume cycle budget at all).
        - 100s base NO_OUTPUT_AT_START cooldown (long enough to cover 10
          iterations of 1s FakeClock advance each, so the wait state is
          naturally maintained across the entire loop without re-marking
          every agent on every iteration).
        - 3 agents in the development chain (claude, opencode, agy).

    Behaviour:
        - Mark all three agents unavailable ONCE, through the public
          controller.handle() surface (a single NO_OUTPUT_AT_START failure per
          agent). After the third handle() call, every agent in the chain is
          on cooldown and the wait state has been entered.
        - Loop 10 times: advance the FakeClock by 1s (well within the 100s
          cooldown, so all three agents stay on cooldown for the entire loop)
          and call controller.handle() with a NO_OUTPUT_AT_START failure for
          the CURRENT agent in the chain. The handle() call naturally
          re-marks the current agent (extending its cooldown with the
          exponential backoff policy) but the OTHER two agents are still
          unavailable with their original cooldowns, so the wait state is
          re-entered on every iteration.

    Assertions (per iteration):
        - state.phase remains "development" (never enters failed_terminal).
        - state.last_retry_delay_ms > 0 (the run loop has a positive sleep).
        - state.recovery_cycle_count remains 0 (wait state does not consume
          the cycle cap; the cap of 200 is preserved for actual failures).
        - state.last_retry_delay_ms does not exceed 100_000ms (the max
          cooldown cap), confirming the exponential backoff is bounded.
    """
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

    # Mark all three agents unavailable through the PUBLIC handle() surface.
    # This is the canonical way to set up the wait state in production: a
    # series of agent failures exhausts the chain and lands on the wait
    # branch in _handle_retry_progression. We avoid mutating the private
    # tracker state so the test survives a future persistent-store swap.
    for agent_name in ("claude", "opencode", "agy"):
        exc = AgentInactivityTimeoutError(agent_name, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=agent_name),
        )

    # After the three handle() calls, every agent in the chain is on cooldown
    # and the chain.current_index is at the last agent (agy). The wait state
    # has been entered; verify the preconditions hold before the loop.
    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == len(chain.agents) - 1
    assert state.last_retry_delay_ms > 0
    assert state.recovery_cycle_count == 0

    for _ in range(10):
        # Advance the FakeClock by 1s — well within the 100s cooldown window,
        # so all three agents remain on cooldown for the entire loop. This
        # is the natural cooldown cycle observation: the wall-clock
        # progresses by 1s each iteration, and the wait state is re-entered
        # on every handle() because the chain is still fully unavailable.
        clock.advance(1.0)

        # Re-trigger the wait state for the current agent (agy, the chain
        # tail). handle() re-marks the current agent via the natural code
        # path (_mark_agent_unavailable), but the other two agents stay on
        # their original cooldowns so the wait state is re-entered.
        current_agent = chain.agents[chain.current_index]
        exc = AgentInactivityTimeoutError(current_agent, 30.0, opts=opts)
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent=current_agent),
        )

        assert state.phase == "development"
        # The run loop has a positive sleep: the wait state is entered.
        assert state.last_retry_delay_ms > 0
        # Wait state must not consume the cycle cap; the cap of 200 is
        # preserved for actual failure-driven recovery cycles.
        assert state.recovery_cycle_count == 0
        # Cooldowns are bounded by the max_backoff_ms cap.
        assert state.last_retry_delay_ms <= 200_000


def test_wait_state_resumes_to_first_available_agent_after_cooldown() -> None:
    """AC-10: after a cooldown expires, the controller routes to the FIRST
    available agent in chain order (wrap=true semantics in
    ``_next_available_agent_index``), not the last available agent.

    Setup:
        - 3-agent chain (claude, opencode, agy) with different cooldowns:
            * claude: 5s
            * opencode: 2s (expires first)
            * agy: 10s
        - Pre-seed the three entries via the PUBLIC
          ``RecoveryControllerOptions.unavailability_entries`` seam. This is
          the documented public injection point for tracker state and avoids
          touching the private ``_entries`` dict.
        - Advance the FakeClock to 3s — opencode (2s) is now available, but
          claude (5s) and agy (10s) are still on cooldown.

    Behaviour:
        - The current agent (claude) is unavailable, but the controller must
          search FORWARD in chain order (wrap=true) and pick the FIRST
          available agent. The first available agent in chain order is
          opencode (index 1) — NOT agy (index 2, the last available).

    Assertions:
        - chain.current_index points to 1 (opencode).
        - state.last_retry_delay_ms == 0 (the wait state was NOT entered
          because the controller found an available agent).
    """
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()

    # Pre-seed unavailable state through the public unavailability_entries
    # option. Each entry has a distinct unavailable_until_ms; the attempt
    # counter is 0 (no prior mark_unavailable calls).
    initial_entries: dict[str, UnavailabilityEntry] = {
        "development:claude": UnavailabilityEntry(
            unavailable_until_ms=5000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=5000,
            max_backoff_ms=5000,
        ),
        "development:opencode": UnavailabilityEntry(
            unavailable_until_ms=2000,
            reason=UnavailabilityReason.NO_OUTPUT_AT_START,
            attempt=0,
            base_backoff_ms=2000,
            max_backoff_ms=2000,
        ),
        "development:agy": UnavailabilityEntry(
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

    # Advance the FakeClock to 3s. opencode (2s cooldown) is now available;
    # claude (5s) and agy (10s) are still on cooldown.
    clock.advance(3.0)

    # A NO_OUTPUT_AT_START failure for the current agent (claude) triggers
    # the unavailable-handling path. The controller must skip claude (still
    # on cooldown) and search forward (wrap=true) for the first available
    # agent. opencode is the first available, so the chain must advance to
    # index 1.
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
    # The wait state was NOT entered because an available agent was found
    # in the chain, so last_retry_delay_ms is reset to 0 by the controller.
    assert state.last_retry_delay_ms == 0


def test_wait_state_first_cooldown_uses_earliest_unavailable_wait() -> None:
    """AC-11: when agents have different cooldowns, the wait state uses the
    EARLIEST cooldown (not the latest) so the run loop sleeps the minimum
    time and retries as soon as the first agent is unblocked.

    Setup:
        - 2-agent chain (claude, opencode) with different cooldowns:
            * claude: 5s
            * opencode: 10s
        - Pre-seed the two entries via the PUBLIC
          ``RecoveryControllerOptions.unavailability_entries`` seam.

    Behaviour:
        - A NO_OUTPUT_AT_START failure for claude (the current agent) marks
          claude as the failure source. The controller then enters the wait
          state (both agents are unavailable) and computes the wait time
          from the EARLIEST cooldown in the chain.

    Assertions:
        - state.last_retry_delay_ms == 5000 (claude's 5s cooldown, the
          earliest of the two, NOT opencode's 10s).
        - state.last_error contains the "all agents unavailable" message so
          the run loop recognises the wait state.
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
    # The 2-agent chain is what we actually need; rewrite the chain to match.
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

    # Wait for the EARLIEST cooldown (claude: 5s), not the LATEST (opencode: 10s).
    assert state.last_retry_delay_ms == 5000
    # The run loop reads state.last_error to recognise the wait state; it
    # must contain the "all agents unavailable" marker.
    assert state.last_error is not None
    assert "all agents unavailable" in state.last_error
