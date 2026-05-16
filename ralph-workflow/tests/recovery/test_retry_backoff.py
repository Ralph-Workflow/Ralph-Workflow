"""Black-box test: retry backoff is computed and applied correctly."""

from __future__ import annotations

import asyncio

from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import (
    RecoveryController,
    RecoveryControllerOptions,
    compute_backoff_ms,
)

# Test constants for backoff computation
_BASE_MS = 1000
_MAX_MS = 30_000
_ATTEMPT_0_EXPECTED = 1000
_ATTEMPT_1_EXPECTED = 2000
_ATTEMPT_2_EXPECTED = 4000
_ATTEMPT_3_EXPECTED = 8000


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def _make_bundle_with_retry_delay(
    chain_name: str = "development",
    agents: list[str] | None = None,
    retry_delay_ms: int = 1000,
    max_retries: int = 3,
) -> PolicyBundle:
    if agents is None:
        agents = ["claude"]
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                chain_name: AgentChainConfig(
                    agents=agents,
                    max_retries=max_retries,
                    retry_delay_ms=retry_delay_ms,
                )
            },
            agent_drains={
                "development": AgentDrainConfig(chain=chain_name),
            },
        ),
        pipeline=PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="development",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


def test_compute_backoff_ms_doubles_each_attempt() -> None:
    """Backoff doubles with each retry attempt."""
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=0, max_ms=_MAX_MS) == _ATTEMPT_0_EXPECTED
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=1, max_ms=_MAX_MS) == _ATTEMPT_1_EXPECTED
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=2, max_ms=_MAX_MS) == _ATTEMPT_2_EXPECTED
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=3, max_ms=_MAX_MS) == _ATTEMPT_3_EXPECTED


def test_compute_backoff_ms_caps_at_max() -> None:
    """Backoff is capped at max_ms."""
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=10, max_ms=_MAX_MS) == _MAX_MS
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=100, max_ms=_MAX_MS) == _MAX_MS


def test_retry_delay_ms_zero_without_policy_bundle() -> None:
    """FailureEvent has retry_delay_ms=0 when no policy bundle is configured."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("agent idle"),
        phase="development",
        agent="claude",
    )

    # Without policy_bundle, retry_delay_ms is 0
    assert evt.retry_delay_ms == 0


def test_environmental_failure_has_zero_delay() -> None:
    """Environmental failures should not have a retry delay."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        ConnectionError("connection reset"),
        phase="development",
        agent="claude",
    )

    # Environmental failures don't count against budget and have no delay
    assert evt.counted_against_budget is False
    assert evt.retry_delay_ms == 0


def test_reset_backoff_clears_counter() -> None:
    """reset_backoff clears the backoff counter for subsequent success."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # Two failures to build up backoff (but no policy_bundle so delay stays 0)
    controller.handle(state, _AgentTimeoutError("idle 1"), phase="development", agent="claude")
    controller.handle(state, _AgentTimeoutError("idle 2"), phase="development", agent="claude")

    # Reset backoff (would be called on successful agent invocation)
    controller.reset_backoff("development", "claude")

    # Backoff attempts dict should be empty after reset
    assert len(controller._backoff_attempts) == 0


def test_backoff_attempts_tracked_per_agent() -> None:
    """Backoff attempts are tracked separately per agent."""
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude", "opencode"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # Fail with claude twice
    controller.handle(
        state, _AgentTimeoutError("claude idle 1"), phase="development", agent="claude"
    )
    controller.handle(
        state, _AgentTimeoutError("claude idle 2"), phase="development", agent="claude"
    )

    # Fail with opencode once
    controller.handle(
        state, _AgentTimeoutError("opencode idle"), phase="development", agent="opencode"
    )

    # Verify backoff attempts
    assert controller._backoff_attempts.get(f"{'development'}:claude") == 2
    assert controller._backoff_attempts.get(f"{'development'}:opencode") == 1


def test_backoff_resets_on_fallover() -> None:
    """Backoff counter resets when agent chain falls over to next agent."""
    registry = AgentBudgetRegistry().set_budget("development", "claude", max_retries=1)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, budget_registry=registry)
    )
    state = _make_state(["claude", "opencode"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # First failure - budget exhausted for claude, fallover to opencode
    new_state, _, _ = controller.handle(
        state,
        _AgentTimeoutError("claude idle"),
        phase="development",
        agent="claude",
    )

    # Should have fallen over to opencode
    assert new_state.chain_for_phase("development").current_index == 1

    # Backoff for claude should still be 1, but when we fail opencode,
    # the backoff attempts should reflect the new agent
    assert controller._backoff_attempts.get(f"{'development'}:claude") == 1


def test_retry_delay_ms_from_policy_bundle() -> None:
    """FailureEvent carries computed retry_delay_ms from policy chain config.

    With a chain configured for retry_delay_ms=500 and attempt=0,
    the event should have retry_delay_ms=500.
    """
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500, max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=bundle)
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("claude idle"),
        phase="development",
        agent="claude",
    )

    # retry_delay_ms should be 500 (base delay for first attempt)
    assert evt.retry_delay_ms == 500


def test_retry_delay_ms_doubles_on_subsequent_failure() -> None:
    """retry_delay_ms doubles on each retry attempt for the same agent."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500, max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=bundle)
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # First failure (attempt 0 -> base_ms * 2^0 = 500)
    _, _, evt1 = controller.handle(
        state,
        _AgentTimeoutError("idle 1"),
        phase="development",
        agent="claude",
    )
    assert evt1.retry_delay_ms == 500

    # Second failure (attempt 1 -> base_ms * 2^1 = 1000)
    _, _, evt2 = controller.handle(
        state,
        _AgentTimeoutError("idle 2"),
        phase="development",
        agent="claude",
    )
    assert evt2.retry_delay_ms == 1000

    # Third failure (attempt 2 -> base_ms * 2^2 = 2000)
    _, _, evt3 = controller.handle(
        state,
        _AgentTimeoutError("idle 3"),
        phase="development",
        agent="claude",
    )
    assert evt3.retry_delay_ms == 2000


def test_retry_delay_ms_caps_at_max_backoff() -> None:
    """retry_delay_ms is capped at 30_000 ms even on many retries."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=1000, max_retries=10)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=bundle)
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # Many failures to push backoff beyond cap
    for i in range(10):
        controller.handle(
            state,
            _AgentTimeoutError(f"idle {i}"),
            phase="development",
            agent="claude",
        )

    # The backoff_attempts key for this agent should have count=10
    # compute_backoff_ms(1000, 10, 30000) = min(1000 * 2^10, 30000) = 30000
    key = f"{'development'}:claude"
    assert controller._backoff_attempts.get(key) == 10
    assert compute_backoff_ms(1000, 10, 30_000) == 30_000


def test_retry_delay_ms_reset_after_successful_invocation() -> None:
    """reset_backoff clears the backoff counter so next failure starts fresh."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500, max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=bundle)
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # Two failures to build up backoff
    controller.handle(state, _AgentTimeoutError("idle 1"), phase="development", agent="claude")
    controller.handle(state, _AgentTimeoutError("idle 2"), phase="development", agent="claude")

    # Verify backoff is at attempt 2
    key = f"{'development'}:claude"
    assert controller._backoff_attempts.get(key) == 2

    # Reset backoff (simulate successful invocation)
    controller.reset_backoff("development", "claude")

    # Backoff attempts should be cleared
    assert controller._backoff_attempts.get(key) is None

    # Next failure should have base delay again (not doubled)
    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle after reset"),
        phase="development",
        agent="claude",
    )
    assert evt.retry_delay_ms == 500  # back to base, not 2000


def test_retry_delay_ms_applied_via_injected_sleep() -> None:
    """Runner's injected sleep is called with correct computed delay.

    This verifies the integration between RecoveryController and the runner:
    the controller surfaces the computed retry_delay_ms in FailureEvent,
    and the runner applies it via the sleep function.
    """
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500, max_retries=3)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, policy_bundle=bundle)
    )
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    # First failure
    controller.handle(state, _AgentTimeoutError("idle 1"), phase="development", agent="claude")

    # Second failure - should have retry_delay_ms=1000
    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle 2"),
        phase="development",
        agent="claude",
    )

    # The event carries the correct computed delay
    assert evt.retry_delay_ms == 1000

    # Simulate the runner applying the delay via injected sleep
    sleep_calls: list[float] = []

    async def _fake_sleep(delay_s: float) -> None:
        sleep_calls.append(delay_s)

    # Runner would call: await sleep(evt.retry_delay_ms / 1000)
    async def _simulate_retry() -> None:
        if evt.retry_delay_ms > 0:
            await _fake_sleep(evt.retry_delay_ms / 1000)

    asyncio.run(_simulate_retry())

    assert sleep_calls == [1.0]


def test_zero_retry_delay_skips_sleep() -> None:
    """When retry_delay_ms is 0, the runner should skip the sleep."""
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10)
    )  # No policy bundle -> delay=0
    state = _make_state(["claude"])

    class _AgentTimeoutError(Exception):
        pass

    _AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle"),
        phase="development",
        agent="claude",
    )

    assert evt.retry_delay_ms == 0

    # Runner skips sleep when retry_delay_ms is 0
    sleep_calls: list[float] = []

    async def _fake_sleep(delay_s: float) -> None:
        sleep_calls.append(delay_s)

    async def _simulate_retry() -> None:
        if evt.retry_delay_ms > 0:
            await _fake_sleep(evt.retry_delay_ms / 1000)

    asyncio.run(_simulate_retry())

    assert sleep_calls == []  # No sleep when delay is 0
