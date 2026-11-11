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

_BASE_MS = 1000
_MAX_MS = 30_000
_ATTEMPT_0_EXPECTED = 1000
_ATTEMPT_1_EXPECTED = 2000
_ATTEMPT_2_EXPECTED = 4000
_ATTEMPT_3_EXPECTED = 8000


class _AgentTimeoutError(Exception):
    pass


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
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=0, max_ms=_MAX_MS) == _ATTEMPT_0_EXPECTED
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=1, max_ms=_MAX_MS) == _ATTEMPT_1_EXPECTED
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=2, max_ms=_MAX_MS) == _ATTEMPT_2_EXPECTED
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=3, max_ms=_MAX_MS) == _ATTEMPT_3_EXPECTED


def test_compute_backoff_ms_caps_at_max() -> None:
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=10, max_ms=_MAX_MS) == _MAX_MS
    assert compute_backoff_ms(base_ms=_BASE_MS, attempt=100, max_ms=_MAX_MS) == _MAX_MS


def test_retry_delay_ms_zero_without_policy_bundle() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("agent idle"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.retry_delay_ms == 0


def test_environmental_failure_has_zero_delay() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        ConnectionError("connection reset"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.counted_against_budget is False
    assert evt.retry_delay_ms == 0


def test_reset_backoff_restores_the_base_delay() -> None:
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

    controller.handle(
        state, _AgentTimeoutError("idle 1"), FailureContext(phase="development", agent="claude")
    )
    _, _, grown = controller.handle(
        state, _AgentTimeoutError("idle 2"), FailureContext(phase="development", agent="claude")
    )
    assert grown.retry_delay_ms == 1000

    controller.reset_backoff("development", "claude")

    _, _, after_reset = controller.handle(
        state, _AgentTimeoutError("idle 3"), FailureContext(phase="development", agent="claude")
    )
    assert after_reset.retry_delay_ms == 500


def test_backoff_grows_per_agent() -> None:
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

    _, _, opencode_first = controller.handle(
        state,
        _AgentTimeoutError("opencode idle"),
        FailureContext(phase="development", agent="opencode"),
    )

    assert claude_first.retry_delay_ms == 500
    assert claude_second.retry_delay_ms == 1000
    assert opencode_first.retry_delay_ms == 500


def test_ledger_demotion_falls_over_to_the_next_agent() -> None:
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

    chain = new_state.chain_for_phase("development")
    assert chain is not None
    assert chain.current_index == 1
    assert chain.retries == 0


def test_retry_delay_ms_from_policy_bundle() -> None:
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

    assert evt.retry_delay_ms == 500


def test_retry_delay_ms_doubles_on_subsequent_failure() -> None:
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

    _, _, evt1 = controller.handle(
        state,
        _AgentTimeoutError("idle 1"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt1.retry_delay_ms == 500

    _, _, evt2 = controller.handle(
        state,
        _AgentTimeoutError("idle 2"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt2.retry_delay_ms == 1000

    _, _, evt3 = controller.handle(
        state,
        _AgentTimeoutError("idle 3"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt3.retry_delay_ms == 2000


def test_retry_delay_ms_caps_at_max_backoff() -> None:
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

    delay_ms = 0
    for i in range(10):
        _, _, evt = controller.handle(
            state,
            _AgentTimeoutError(f"idle {i}"),
            FailureContext(phase="development", agent="claude"),
        )
        delay_ms = evt.retry_delay_ms

    assert delay_ms == 30_000
    assert compute_backoff_ms(1000, 10, 30_000) == 30_000


def test_retry_delay_ms_reset_after_successful_invocation() -> None:
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

    _, _, grown = controller.handle(
        state, _AgentTimeoutError("idle 1"), FailureContext(phase="development", agent="claude")
    )
    assert grown.retry_delay_ms == 500
    _, _, grown = controller.handle(
        state, _AgentTimeoutError("idle 2"), FailureContext(phase="development", agent="claude")
    )
    assert grown.retry_delay_ms == 1000

    controller.reset_backoff("development", "claude")

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle after reset"),
        FailureContext(phase="development", agent="claude"),
    )
    assert evt.retry_delay_ms == 500


def test_retry_delay_ms_applied_via_injected_sleep() -> None:
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

    controller.handle(
        state, _AgentTimeoutError("idle 1"), FailureContext(phase="development", agent="claude")
    )

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle 2"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.retry_delay_ms == 1000

    sleep_calls: list[float] = []

    async def _fake_sleep(delay_s: float) -> None:
        sleep_calls.append(delay_s)

    async def _simulate_retry() -> None:
        if evt.retry_delay_ms > 0:
            await _fake_sleep(evt.retry_delay_ms / 1000)

    asyncio.run(_simulate_retry())

    assert sleep_calls == [1.0]


def test_zero_retry_delay_skips_sleep() -> None:
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))
    state = _make_state(["claude"])

    _, _, evt = controller.handle(
        state,
        _AgentTimeoutError("idle"),
        FailureContext(phase="development", agent="claude"),
    )

    assert evt.retry_delay_ms == 0

    sleep_calls: list[float] = []

    async def _fake_sleep(delay_s: float) -> None:
        sleep_calls.append(delay_s)

    async def _simulate_retry() -> None:
        if evt.retry_delay_ms > 0:
            await _fake_sleep(evt.retry_delay_ms / 1000)

    asyncio.run(_simulate_retry())

    assert sleep_calls == []


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
