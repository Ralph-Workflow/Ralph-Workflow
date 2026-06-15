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


def test_controller_mark_agent_unavailable_caps_return_value_at_30_minutes() -> None:
    """The private ``_mark_agent_unavailable`` helper documents that it
    returns the computed backoff in ms, capped at the reason's
    ``max_backoff_ms``. The store state is correctly capped (the previous
    test exercises that), but the helper's RETURN value was uncapped
    because it returned ``base_backoff_ms * multiplier`` without
    reapplying the cap. A future caller that consumes the return value
    (e.g. for telemetry, for a wait-state that consumes the helper
    directly, for a side-channel log) would see values above 1_800_000ms
    even though the store recorded 1_800_000ms -- a contract violation
    that would mislead operators.

    This test drives repeated ``OUT_OF_CREDITS`` marks through the cap
    and asserts the helper never returns above 1_800_000ms. It also
    asserts the helper's return value matches the cooldown recorded in
    the store so the helper and the store never disagree.
    """
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
    out_of_credits_max = 1_800_000

    # 8 successive marks is more than enough to drive base*2^attempt past
    # the 1_800_000ms cap (60_000 * 2^7 = 7_680_000ms).
    for _ in range(8):
        helper_return = controller._mark_agent_unavailable(
            "development", "claude", UnavailabilityReason.OUT_OF_CREDITS,
        )
        # The helper return value must NEVER exceed the cap.
        assert helper_return <= out_of_credits_max, (
            f"_mark_agent_unavailable returned {helper_return}ms, "
            f"must be capped at {out_of_credits_max}ms"
        )
        # The helper return value must agree with the store-recorded cooldown.
        snap = controller._unavailability_tracker.snapshot()
        stored_timeout = snap["unavailable_timeouts"]["development:claude"]
        current_time_ms = int(clock.monotonic() * 1000)
        stored_remaining = stored_timeout - current_time_ms
        assert helper_return == stored_remaining, (
            f"helper return {helper_return}ms disagrees with store "
            f"remaining {stored_remaining}ms"
        )
        # Advance the clock past the cap so the next mark is from a fresh
        # exponential attempt (otherwise the tracker would refuse to mark
        # the agent again because the entry is still alive).
        clock.advance(out_of_credits_max / 1000.0)
