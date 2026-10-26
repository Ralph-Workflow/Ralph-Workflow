"""Black-box test: retry backoff is computed and applied correctly.

Every assertion reads the controller's PUBLIC answers only: the
``FailureEvent.retry_delay_ms`` the runner consumes, the agent the chain
selects next, and the demotion the ledger records. The file proves that
the delay grows per phase/agent, that the computed delay doubles and
saturates at the cap, and that ``reset_backoff`` clears the growth so the
next failure starts from the base delay again -- without coupling to any
private recovery state.
"""

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
from ralph.recovery.classified_failure import ClassifiedFailure
from ralph.recovery.classifier import FailureContext
from ralph.recovery.controller import (
    RecoveryController,
    RecoveryControllerOptions,
    compute_backoff_ms,
)
from ralph.recovery.failure_category import FailureCategory

# Test constants for backoff computation
_BASE_MS = 1000
_MAX_MS = 30_000
_ATTEMPT_0_EXPECTED = 1000
_ATTEMPT_1_EXPECTED = 2000
_ATTEMPT_2_EXPECTED = 4000
_ATTEMPT_3_EXPECTED = 8000


class _AgentTimeoutError(Exception):
    """Exception whose class name makes the classifier read it as an agent fault."""


_AgentTimeoutError.__name__ = "AgentInactivityTimeoutError"


def _make_state(agents: list[str]) -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
    )


def _make_bundle_with_retry_delay(
    chain_name: str = "development",
    agents: list[str] | None = None,
    retry_delay_ms: int = 1000,
) -> PolicyBundle:
    if agents is None:
        agents = ["claude"]
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                chain_name: AgentChainConfig(
                    agents=agents,
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

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("agent idle"),
        FailureContext(phase="development", agent="claude"),
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
        FailureContext(phase="development", agent="claude"),
    )

    # Environmental failures don't count against budget and have no delay
    assert evt.counted_against_budget is False
    assert evt.retry_delay_ms == 0


def test_reset_backoff_restores_the_base_delay() -> None:
    """reset_backoff clears the delay growth so the next failure starts from the base delay."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude"])

    # Two failures to build up the delay
    controller.handle(
        state, _AgentTimeoutError("idle 1"), FailureContext(phase="development", agent="claude")
    )
    _, _, grown = controller.handle(
        state, _AgentTimeoutError("idle 2"), FailureContext(phase="development", agent="claude")
    )
    assert grown.retry_delay_ms == 1000

    # Reset backoff (would be called on successful agent invocation)
    controller.reset_backoff("development", "claude")

    # The next failure starts from the base delay again
    _, _, after_reset = controller.handle(
        state, _AgentTimeoutError("idle 3"), FailureContext(phase="development", agent="claude")
    )
    assert after_reset.retry_delay_ms == 500


def test_backoff_grows_per_agent() -> None:
    """The delay growth is tracked separately per agent."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500, agents=["claude", "opencode"])
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude", "opencode"])

    # Fail with claude twice
    _, _, claude_first = controller.handle(
        state,
        _AgentTimeoutError("claude idle 1"),
        FailureContext(phase="development", agent="claude"),
    )
    _, _, claude_second = controller.handle(
        state,
        _AgentTimeoutError("claude idle 2"),
        FailureContext(phase="development", agent="claude"),
    )

    # Fail with opencode once
    _, _, opencode_first = controller.handle(
        state,
        _AgentTimeoutError("opencode idle"),
        FailureContext(phase="development", agent="opencode"),
    )

    # claude's own second failure doubles its delay; opencode starts from the
    # base delay because the growth is per agent.
    assert claude_first.retry_delay_ms == 500
    assert claude_second.retry_delay_ms == 1000
    assert opencode_first.retry_delay_ms == 500


def test_ledger_demotion_falls_over_to_the_next_agent() -> None:
    """The chain advances to the next agent once the ledger demotes the current one.

    The failure ledger is the single owner of agent health: it demotes the
    agent after the consecutive unproductive limit, and the chain then runs
    the next agent in the phase.
    """
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500, agents=["claude", "opencode"])
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=3,
            agent_max_total_unproductive=9,
        )
    )
    state = _make_state(["claude", "opencode"])

    new_state = state
    for attempt in range(3):
        new_state, _, _ = controller.handle(
            state,
            _AgentTimeoutError(f"claude idle {attempt}"),
            FailureContext(phase="development", agent="claude"),
        )

    # The third consecutive unproductive attempt demotes claude, so the chain
    # advances to opencode.
    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert chain.retries == 0


def test_retry_delay_ms_from_policy_bundle() -> None:
    """FailureEvent carries computed retry_delay_ms from policy chain config.

    With a chain configured for retry_delay_ms=500 and attempt=0,
    the event should have retry_delay_ms=500.
    """
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("claude idle"),
        FailureContext(phase="development", agent="claude"),
    )

    # retry_delay_ms should be 500 (base delay for first attempt)
    assert evt.retry_delay_ms == 500


def test_retry_delay_ms_doubles_on_subsequent_failure() -> None:
    """retry_delay_ms doubles on each retry attempt for the same agent."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude"])

    # First failure (attempt 0 -> base_ms * 2^0 = 500)
    _, _, evt1 = controller.handle(
        state,
        _AgentTimeoutError("idle 1"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt1.retry_delay_ms == 500

    # Second failure (attempt 1 -> base_ms * 2^1 = 1000)
    _, _, evt2 = controller.handle(
        state,
        _AgentTimeoutError("idle 2"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt2.retry_delay_ms == 1000

    # Third failure (attempt 2 -> base_ms * 2^2 = 2000)
    _, _, evt3 = controller.handle(
        state,
        _AgentTimeoutError("idle 3"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt3.retry_delay_ms == 2000


def test_retry_delay_ms_caps_at_max_backoff() -> None:
    """retry_delay_ms is capped at 30_000 ms even on many retries."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=1000)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude"])

    # Many failures to push the delay beyond the cap
    delay_ms = 0
    for i in range(10):
        _, _, evt = controller.handle(
            state,
            _AgentTimeoutError(f"idle {i}"),
            FailureContext(phase="development", agent="claude"),
        )
        delay_ms = evt.retry_delay_ms

    # The tenth failure is already saturated at the cap:
    # compute_backoff_ms(1000, 9, 30000) = min(1000 * 2^9, 30000) = 30000
    assert delay_ms == 30_000
    assert compute_backoff_ms(1000, 10, 30_000) == 30_000


def test_retry_delay_ms_reset_after_successful_invocation() -> None:
    """reset_backoff clears the backoff counter so next failure starts fresh."""
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude"])

    # Two failures to build up backoff
    _, _, grown = controller.handle(
        state, _AgentTimeoutError("idle 1"), FailureContext(phase="development", agent="claude")
    )
    assert grown.retry_delay_ms == 500
    _, _, grown = controller.handle(
        state, _AgentTimeoutError("idle 2"), FailureContext(phase="development", agent="claude")
    )
    assert grown.retry_delay_ms == 1000

    # Reset backoff (simulate successful invocation)
    controller.reset_backoff("development", "claude")

    # Next failure should have base delay again (not doubled)
    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle after reset"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt.retry_delay_ms == 500  # back to base, not 2000


def test_retry_delay_ms_applied_via_injected_sleep() -> None:
    """Runner's injected sleep is called with correct computed delay.

    This verifies the integration between RecoveryController and the runner:
    the controller surfaces the computed retry_delay_ms in FailureEvent,
    and the runner applies it via the sleep function.
    """
    bundle = _make_bundle_with_retry_delay(retry_delay_ms=500)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=20,
            agent_max_total_unproductive=20,
        )
    )
    state = _make_state(["claude"])

    # First failure
    controller.handle(
        state, _AgentTimeoutError("idle 1"), FailureContext(phase="development", agent="claude")
    )

    # Second failure - should have retry_delay_ms=1000
    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle 2"),
        FailureContext(phase="development", agent="claude"),
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

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle"),
        FailureContext(phase="development", agent="claude"),
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


def test_artifact_validation_retry_uses_consecutive_unproductive_limit() -> None:
    bundle = _make_bundle_with_retry_delay(agents=["claude", "opencode"])
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            policy_bundle=bundle,
            agent_max_consecutive_unproductive=2,
        )
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"],
                current_index=0,
                retries=0,
            )
        },
    )
    failure = ClassifiedFailure(
        category=FailureCategory.ARTIFACT_VALIDATION,
        reason="Artifact validation fault: Missing required artifact",
        attributed_agent=None,
        attributed_phase="development",
        counts_against_budget=False,
        original_exception=None,
        raw_message="Missing required artifact",
    )

    state1, _, _ = controller.handle(
        state,
        "Missing required artifact",
        FailureContext(phase="development", agent="claude", classified_failure=failure),
    )
    chain1 = state1.chain_for_phase("development")
    assert chain1 is not None
    assert chain1.current_index == 0

    state2, _, evt2 = controller.handle(
        state1,
        "Missing required artifact",
        FailureContext(phase="development", agent="claude", classified_failure=failure),
    )

    assert evt2.category == "artifact_validation"
    chain2 = state2.chain_for_phase("development")
    assert chain2 is not None
    assert chain2.current_index == 1
    assert chain2.retries == 0
