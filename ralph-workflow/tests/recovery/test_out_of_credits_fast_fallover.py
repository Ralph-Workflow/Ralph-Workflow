from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.timeout_clock import FakeClock
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
    chain = state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert controller.unavailability_store.is_available("development", "opencode") is True
    assert (
        controller.unavailability_store.earliest_unavailable_wait_ms("development", ["claude"])
        == 60_000
    )


def test_recovery_classifier_regression_codex_at_capacity_falls_over_with_credits_backoff() -> None:
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
    assert (
        controller.unavailability_store.earliest_unavailable_wait_ms("development", ["codex"])
        == 60_000
    )


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

    expected_cooldowns = [min(60_000 * (1 << attempt), 18_000_000) for attempt in range(12)]

    for i in range(len(expected_cooldowns)):
        state, _effects, _failure_evt = controller.handle(
            state,
            exc,
            FailureContext(phase="development", agent="claude"),
        )
        cooldown = controller.unavailability_store.earliest_unavailable_wait_ms(
            "development", ["claude"]
        )
        assert cooldown == expected_cooldowns[i]

        clock.advance(expected_cooldowns[i] / 1000.0)


def test_unavailability_store_mark_unavailable_caps_recorded_backoff_at_five_hours() -> None:
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

    for _ in range(12):
        entry = controller.unavailability_store.mark_unavailable(
            "development",
            "claude",
            UnavailabilityReason.OUT_OF_CREDITS,
        )
        recorded_backoff_ms = min(
            entry.base_backoff_ms * (1 << entry.attempt),
            entry.max_backoff_ms,
        )
        assert recorded_backoff_ms <= out_of_credits_max, (
            f"mark_unavailable recorded {recorded_backoff_ms}ms, "
            f"must be capped at {out_of_credits_max}ms"
        )
        stored_remaining = controller.unavailability_store.earliest_unavailable_wait_ms(
            "development", ["claude"]
        )
        assert recorded_backoff_ms == stored_remaining, (
            f"recorded backoff {recorded_backoff_ms}ms disagrees with "
            f"store remaining {stored_remaining}ms"
        )
        clock.advance(out_of_credits_max / 1000.0)
