"""Regression for the Cursor auth failure that bypassed agent cooldown.

The observed incident classified ``cursor/auto`` authentication failure as
``USER_CONFIG`` with ``counted=False`` and ended the phase without marking the
agent unavailable. The next selection therefore chose the configured head
agent again instead of the fallback.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.agents.invoke import (
    AgentInvocationError,
    BrokenAgentExitError,
    PiContextExhaustedExitError,
    PiProviderFailureExitError,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline import effect_router
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.classifier import FailureCategory, FailureClassifier, FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason


def _cursor_auth_failure() -> BrokenAgentExitError:
    return BrokenAgentExitError(
        "cursor/auto",
        reason="no_output",
        stderr="Authentication required. Please run 'agent login' first.",
    )


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            policy_bundle=load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults"),
        )
    )


def _state(phase: str = "development") -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={
            phase: AgentChainState(
                agents=["cursor/auto", "fallback"],
                current_index=0,
                retries=0,
            )
        },
    )


def _cooldown_remaining_ms(
    controller: RecoveryController,
    clock: FakeClock,
    _phase: str = "development",
    agent: str = "cursor/auto",
) -> int:
    snapshot = controller.unavailability_store.snapshot()
    deadline_ms = snapshot["unavailable_timeouts"][agent]
    assert isinstance(deadline_ms, int)
    return deadline_ms - int(clock.monotonic() * 1000)


def test_cursor_auth_failure_cools_agent_before_user_config_phase_transition() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    new_state, _, event = controller.handle(
        _state(),
        _cursor_auth_failure(),
        FailureContext(phase="development", agent="cursor/auto"),
    )

    assert event.category == str(FailureCategory.USER_CONFIG)
    assert event.counted_against_budget is False
    assert event.unavailability_reason == str(UnavailabilityReason.AUTH_CONFIG)
    assert new_state.phase == "failed_terminal"
    assert _cooldown_remaining_ms(controller, clock) == 5_000

    selection = controller.preferred_agent_index(
        "development",
        ["cursor/auto", "fallback"],
    )

    assert selection.agent == "fallback"
    assert selection.index == 1


def test_cursor_auth_cooldown_grows_after_expiry_caps_and_resets() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    expected_cooldowns_ms = [min(5_000 * 2**attempt, 18_000_000) for attempt in range(15)]
    for expected_cooldown_ms in expected_cooldowns_ms:
        _, _, event = controller.handle(
            _state(),
            _cursor_auth_failure(),
            FailureContext(phase="development", agent="cursor/auto"),
        )

        assert event.category == str(FailureCategory.USER_CONFIG)
        assert event.counted_against_budget is False
        assert _cooldown_remaining_ms(controller, clock) == expected_cooldown_ms
        clock.advance(expected_cooldown_ms / 1000)

    controller.reset_backoff("development", "cursor/auto")

    assert controller.unavailability_store.is_available("development", "cursor/auto") is True

    _, _, event = controller.handle(
        _state(),
        _cursor_auth_failure(),
        FailureContext(phase="development", agent="cursor/auto"),
    )

    assert event.counted_against_budget is False
    assert _cooldown_remaining_ms(controller, clock) == 5_000


def test_cursor_auth_backoff_history_survives_a_phase_transition() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    controller.handle(
        _state(),
        _cursor_auth_failure(),
        FailureContext(phase="development", agent="cursor/auto"),
    )
    clock.advance(5)

    _, _, event = controller.handle(
        _state("review"),
        _cursor_auth_failure(),
        FailureContext(phase="review", agent="cursor/auto"),
    )

    assert event.counted_against_budget is False
    assert _cooldown_remaining_ms(controller, clock, "review") == 10_000


def test_non_cursor_auth_message_cools_agent_before_fallback_selection() -> None:
    """Plan S-1: authentication messages cool every configured agent."""
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    new_state, _, event = controller.handle(
        _state(),
        "Authentication required. Please run 'agent login' first.",
        FailureContext(phase="development", agent="fallback"),
    )

    assert event.category == str(FailureCategory.USER_CONFIG)
    assert event.counted_against_budget is False
    assert event.unavailability_reason == str(UnavailabilityReason.AUTH_CONFIG)
    assert new_state.phase == "failed_terminal"
    assert _cooldown_remaining_ms(controller, clock, "development", "fallback") == 5_000

    selection = controller.preferred_agent_index(
        "development",
        ["fallback", "cursor/auto"],
    )

    assert selection.agent == "cursor/auto"
    assert selection.index == 1


def test_uncounted_auth_config_failure_logs_one_cooldown_transition() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    logs: list[str] = []
    sink_id = logger.add(logs.append, level="INFO", format="{message}")
    try:
        _, _, event = controller.handle(
            _state(),
            _cursor_auth_failure(),
            FailureContext(phase="development", agent="cursor/auto"),
        )
    finally:
        logger.remove(sink_id)

    assert event.counted_against_budget is False
    cooldown_lines = [line for line in logs if "cursor/auto unavailable:" in line]
    assert len(cooldown_lines) == 1
    assert "auth_config" in cooldown_lines[0]
    assert "cooldown 5000ms active" in cooldown_lines[0]


def test_non_auth_user_config_failure_does_not_cool_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)

    _, _, event = controller.handle(
        _state(),
        "AgentNotFound",
        FailureContext(phase="development", agent="cursor/auto"),
    )

    assert event.category == str(FailureCategory.USER_CONFIG)
    assert event.counted_against_budget is False
    assert controller.unavailability_store.is_available("development", "cursor/auto") is True


def test_terminal_provider_failures_cool_before_fallback_selection() -> None:
    cases = (
        (
            PiProviderFailureExitError(
                "pi/minimax/MiniMax-M3",
                "server cluster is currently under high load (529)",
            ),
            UnavailabilityReason.PROVIDER_UNAVAILABLE,
            60_000,
        ),
        (
            PiProviderFailureExitError(
                "pi/minimax/MiniMax-M3",
                "HTTP 429: too many requests",
            ),
            UnavailabilityReason.OUT_OF_CREDITS,
            60_000,
        ),
        (
            PiContextExhaustedExitError("pi/minimax/MiniMax-M3"),
            UnavailabilityReason.CONTEXT_EXHAUSTED,
            5_000,
        ),
        (
            AgentInvocationError(
                "fallback",
                1,
                'overloaded_error: server cluster is currently under high load (529)',
            ),
            UnavailabilityReason.PROVIDER_UNAVAILABLE,
            60_000,
        ),
        (
            AgentInvocationError("fallback", 1, "HTTP 429: too many requests"),
            UnavailabilityReason.OUT_OF_CREDITS,
            60_000,
        ),
        (
            AgentInvocationError(
                "fallback",
                1,
                "invalid params, context window exceeds limit (2013)",
            ),
            UnavailabilityReason.CONTEXT_EXHAUSTED,
            5_000,
        ),
        (
            AgentInvocationError("fallback", 1),
            UnavailabilityReason.PROCESS_EXITED,
            5_000,
        ),
        (
            AgentInvocationError("fallback", 1, "unexpected EOF"),
            UnavailabilityReason.PROCESS_EXITED,
            5_000,
        ),
    )
    for failure, expected_reason, expected_cooldown_ms in cases:
        _assert_terminal_failure_cools(failure, expected_reason, expected_cooldown_ms)


def _assert_terminal_failure_cools(
    failure: Exception,
    expected_reason: UnavailabilityReason,
    expected_cooldown_ms: int,
) -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    failed_agent = getattr(failure, "agent_name", "fallback")
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=[failed_agent, "healthy-fallback"],
                current_index=0,
                retries=0,
            )
        },
        last_connectivity_state="online",
    )

    _, _, event = controller.handle(
        state,
        failure,
        FailureContext(phase="development", agent=failed_agent),
    )

    assert event.category == str(FailureCategory.AGENT)
    assert event.unavailability_reason == str(expected_reason)
    assert _cooldown_remaining_ms(controller, clock, "development", failed_agent) == (
        expected_cooldown_ms
    )
    selection = controller.preferred_agent_index(
        "development",
        [failed_agent, "healthy-fallback"],
    )
    assert selection.agent == "healthy-fallback"


def test_non_agent_provider_text_does_not_cool_agent() -> None:
    failures = (
        OSError("HTTP 503 from artifact storage"),
        OSError("HTTP 429 from artifact storage"),
        AgentInvocationError(
            "fallback",
            1,
            "HTTP 503 from orchestrator",
            failure_origin="runtime_launch",
        ),
        AgentInvocationError("fallback", 1, "HTTP 429", failure_origin="mcp_operation"),
        AgentInvocationError(
            "fallback", 143, "HTTP 429", failure_origin="intentional_termination"
        ),
        AgentInvocationError(
            "fallback", 1, "HTTP 429", failure_origin="watchdog_observation"
        ),
    )
    for failure in failures:
        classified = FailureClassifier().classify(
            failure,
            phase="development",
            agent="fallback",
            connectivity_state="online",
        )

        assert classified.is_unavailable is False
        assert classified.unavailability_reason is None


def test_fresh_phase_selection_uses_available_fallback_instead_of_chain_head() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    controller.handle(
        _state(),
        _cursor_auth_failure(),
        FailureContext(phase="development", agent="cursor/auto"),
    )

    selected = effect_router._agent_name_for_phase_from_policy(
        PipelineState(phase="development"),
        load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults"),
        recovery=controller,
    )

    assert selected != "cursor/auto"


def test_all_cooling_agents_have_one_earliest_wait_and_no_selection() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    controller.unavailability_store.mark_unavailable(
        "development", "cursor/auto", UnavailabilityReason.AUTH_CONFIG
    )
    controller.unavailability_store.mark_unavailable(
        "development", "fallback", UnavailabilityReason.AUTH_CONFIG
    )

    selection = controller.preferred_agent_index("development", ["cursor/auto", "fallback"])

    assert selection.agent is None
    assert controller.earliest_available_wait_ms("development", ["cursor/auto", "fallback"]) == 5_000


def test_success_reset_isolated_to_the_succeeding_agent() -> None:
    clock = FakeClock(start=0.0)
    controller = _controller(clock)
    for agent in ("cursor/auto", "fallback"):
        controller.unavailability_store.mark_unavailable(
            "development", agent, UnavailabilityReason.AUTH_CONFIG
        )

    controller.reset_backoff("development", "fallback")

    assert controller.unavailability_store.is_available("development", "fallback") is True
    assert controller.unavailability_store.is_available("development", "cursor/auto") is False


def test_cursor_auth_classifier_retains_user_config_without_budget_count() -> None:
    classified = FailureClassifier().classify(
        _cursor_auth_failure(),
        phase="development",
        agent="cursor/auto",
    )

    assert classified.category is FailureCategory.USER_CONFIG
    assert classified.counts_against_budget is False
    assert classified.is_unavailable is True
    assert classified.unavailability_reason is UnavailabilityReason.AUTH_CONFIG
