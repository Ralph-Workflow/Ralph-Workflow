from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
from ralph.checked_accessors import as_int, as_mapping
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEventBus
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _minimal_policy_bundle() -> PolicyBundle:
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
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
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
    assert chain is not None
    assert chain.current_index == 1
    # opencode is available
    assert controller.unavailability_store.is_available("development", "opencode") is True
    # claude has the OUT_OF_CREDITS base timeout (60_000ms)
    snapshot = controller.snapshot()
    timeouts = as_mapping(snapshot["unavailable_timeouts"], field="unavailable_timeouts")
    assert as_int(timeouts["claude"], field="unavailable_timeouts.claude") == 60_000


def test_recovery_classifier_regression_codex_at_capacity_falls_over_with_credits_backoff() -> None:
    """S-2: Codex capacity exhaustion advances to the next available agent."""
    message = "Error: Selected model is at capacity. Please try a different model."
    failure = FailureClassifier().classify(
        AgentInvocationError("codex", 1, message),
        phase="development",
        agent="codex",
        connectivity_state="online",
    )
    assert failure.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS

    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=FailureEventBus(),
        )
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["codex", "opencode"], current_index=0),
        },
    ).copy_with(last_connectivity_state="online")

    state, _effects, failure_evt = controller.handle(
        state,
        AgentInvocationError("codex", 1, message),
        FailureContext(phase="development", agent="codex"),
    )

    assert failure_evt.unavailability_reason == UnavailabilityReason.OUT_OF_CREDITS.value
    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    codex_timeouts = as_mapping(
        controller.snapshot()["unavailable_timeouts"],
        field="unavailable_timeouts",
    )
    assert as_int(codex_timeouts["codex"], field="unavailable_timeouts.codex") == 60_000


def test_out_of_credits_backoff_doubles_each_retry_up_to_five_hour_cap() -> None:
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    chain_state = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development",
        phase_chains={"development": chain_state},
    ).copy_with(last_connectivity_state="online")

    exc = AgentInvocationError("claude", 1, "You've hit your weekly limit")

    # ``1 << attempt`` is ``2 ** attempt`` for a non-negative exponent; the
    # shift keeps the expression integer-typed for the strict type checker.
    expected_cooldowns = [min(60_000 * (1 << attempt), 18_000_000) for attempt in range(12)]

    for i in range(len(expected_cooldowns)):
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="claude"),
        )
        snapshot = controller.snapshot()
        timeouts = as_mapping(snapshot["unavailable_timeouts"], field="unavailable_timeouts")
        current_time_ms = int(clock.monotonic() * 1000)
        cooldown = as_int(timeouts["claude"], field="unavailable_timeouts.claude") - current_time_ms
        assert cooldown == expected_cooldowns[i]

        # Advance the clock past the cooldown so it can be marked again
        clock.advance(expected_cooldowns[i] / 1000.0)


def test_unavailability_store_mark_unavailable_caps_recorded_backoff_at_five_hours() -> None:
    """The public ``unavailability_store`` seam records a capped cooldown.

    ``RecoveryController.unavailability_store.mark_unavailable`` returns the
    frozen ``UnavailabilityEntry`` the controller charges for an
    ``OUT_OF_CREDITS`` agent. The store state is correctly capped (the
    previous test exercises that), but the recorded backoff must be capped
    too: a future caller that consumes the recorded cooldown (e.g. for
    telemetry, for a wait-state that consumes the store directly, for a
    side-channel log) would see values above 18_000_000ms even though the
    store recorded 18_000_000ms -- a contract violation that would mislead
    operators.

    This test drives repeated ``OUT_OF_CREDITS`` marks through the cap
    and asserts the recorded backoff never exceeds 18_000_000ms. It also
    asserts the recorded backoff matches the cooldown recorded in the
    store so the two never disagree.
    """
    clock = FakeClock(start=0.0)
    bus = FailureEventBus()
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            policy_bundle=_minimal_policy_bundle(),
            event_bus=bus,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    out_of_credits_max = 18_000_000

    # Twelve marks exercise growth and repeated saturation at five hours.
    for _ in range(12):
        entry = controller.unavailability_store.mark_unavailable(
            "development",
            "claude",
            UnavailabilityReason.OUT_OF_CREDITS,
        )
        # The recorded backoff is ``base_backoff_ms * 2 ** attempt`` capped at
        # the reason's ``max_backoff_ms`` -- the value the controller charges.
        recorded_backoff_ms = min(
            entry.base_backoff_ms * (1 << entry.attempt),
            entry.max_backoff_ms,
        )
        # The recorded backoff must NEVER exceed the cap.
        assert recorded_backoff_ms <= out_of_credits_max, (
            f"mark_unavailable recorded {recorded_backoff_ms}ms, "
            f"must be capped at {out_of_credits_max}ms"
        )
        # The recorded backoff must agree with the store-recorded cooldown.
        snap = controller.unavailability_store.snapshot()
        timeouts = as_mapping(snap["unavailable_timeouts"], field="unavailable_timeouts")
        stored_timeout = as_int(timeouts["claude"], field="unavailable_timeouts.claude")
        current_time_ms = int(clock.monotonic() * 1000)
        stored_remaining = stored_timeout - current_time_ms
        assert recorded_backoff_ms == stored_remaining, (
            f"recorded backoff {recorded_backoff_ms}ms disagrees with "
            f"store remaining {stored_remaining}ms"
        )
        # Advance the clock past the cap so the next mark is from a fresh
        # exponential attempt (otherwise the tracker would refuse to mark
        # the agent again because the entry is still alive).
        clock.advance(out_of_credits_max / 1000.0)
